"""Public Twilio webhooks for inbound SMS keywords and status callbacks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_consent import ConsentSource
from src.app.services.audit import log_audit
from src.app.services.dead_letter import capture_dead_letter
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.messaging_credentials import TenantTwilioCredentialResolver
from src.app.models.notification import NotificationType
from src.app.services.sms_privacy import (
    hash_for_logging,
    redact_payload,
    safe_error_summary,
)
from src.app.services.sms_service import SmsService
from src.app.services.automation.sms_intent_parser import parse_sms_intent
from src.app.services.usage_metering_service import (
    parse_cost_amount,
    parse_segments,
    record_usage_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio/webhooks", tags=["Twilio Webhooks"])

# Terminal Twilio message statuses. Usage is metered once per message on the
# first terminal callback; the idempotency key (MessageSid) dedupes the
# follow-on "delivered" callback that Twilio sends after "sent".
_TERMINAL_SMS_STATUSES = {"delivered", "sent", "failed", "undelivered"}

def _classify_intent(body: str) -> str:
    """Return STOP/START/HELP if any keyword appears as a whole token.

    STOP wins over START in the unlikely "STOP and START" case — opting out
    is the safer default. HELP is only returned when no opt-out keyword is
    present.
    """
    result = parse_sms_intent(body)
    return result.compliance_keyword or ""


def _classify_confirmation_reply(body: str) -> bool:
    """Return True for a bare confirmation token.

    Mixed replies such as "yes but reschedule" are deliberately not confirmed;
    a false PMS confirmation is more expensive than a missed confirmation.
    """
    return parse_sms_intent(body).intent == "confirm"


@router.post("/inbound-sms")
async def inbound_sms(request: Request) -> Response:
    form = await _verified_form(request)
    from_number = _field(form, "From")
    to_number = _field(form, "To")
    body = (_field(form, "Body") or "").strip()
    parsed_reply = parse_sms_intent(body)
    intent = parsed_reply.compliance_keyword or ""
    keyword = intent  # back-compat with audit metadata that records "keyword"

    async with get_system_db_session(
        "twilio_lookup",
        external_id=to_number,
    ) as session:
        location = await _location_for_twilio_number(session, to_number)
        if not location:
            await capture_dead_letter(
                source="twilio_webhook",
                event_type="inbound_sms_unmatched_location",
                error="Inbound SMS could not be mapped to a location",
                payload=form,
            )
            return _twiml("")

    async with get_system_db_session(
        "twilio",
        institution_id=str(location.institution_id),
        location_id=str(location.id),
        external_id=to_number,
    ) as session:
        compliance = SmsComplianceService(session)

        # Persist every inbound reply (S-2). One intent-classified row per inbound,
        # best-effort correlated to a contact + open run; committed by whichever
        # branch runs below. Does not alter the opt-out/confirm control flow.
        from src.app.services.automation.inbound_sms_routing_service import (
            InboundSmsRoutingService,
        )

        _inbound_msg = await InboundSmsRoutingService(session).record_inbound(
            institution_id=str(location.institution_id),
            location_id=str(location.id),
            from_number=from_number,
            to_number=to_number,
            body=body,
            intent=parsed_reply.intent,
            message_sid=_field(form, "MessageSid"),
        )
        from src.app.services.automation.campaign_response_service import (
            CampaignResponseService,
        )

        _, _handoff = await CampaignResponseService(session).record_sms_response(
            _inbound_msg,
            body=body,
            raw_payload=dict(form),
            parsed=parsed_reply,
        )

        if intent == "STOP":
            await compliance.suppress(
                institution_id=location.institution_id,
                location_id=str(location.id),
                phone=from_number,
                source=ConsentSource.TWILIO_KEYWORD,
                keyword=keyword,
                reason=f"Twilio inbound keyword: {keyword}",
            )
            await _audit_keyword(
                location, from_number, AuditAction.SMS_SUPPRESSION_CREATE, keyword
            )
            await session.commit()
            return _twiml(
                f"You have been opted out of SMS from {location.name}. Reply START to opt back in."
            )

        if intent == "START":
            await compliance.release_suppression(
                institution_id=location.institution_id,
                location_id=str(location.id),
                phone=from_number,
                source=ConsentSource.TWILIO_KEYWORD,
                reason=f"Twilio inbound keyword: {keyword}",
            )
            await _audit_keyword(
                location, from_number, AuditAction.SMS_SUPPRESSION_RELEASE, keyword
            )
            await session.commit()
            return _twiml(
                f"You have been opted in to SMS from {location.name}. Reply STOP to opt out."
            )

        if intent == "HELP":
            await session.commit()
            return _twiml(_help_text(location))

        if from_number and parsed_reply.intent == "confirm":
            from src.app.tasks.automation_workflow import resume_sms_confirmation

            resume_sms_confirmation.delay(
                institution_id=str(location.institution_id),
                location_id=str(location.id),
                from_number=from_number,
                body=body,
                message_sid=_field(form, "MessageSid"),
            )
            await session.commit()
            return _twiml("Thanks, we received your confirmation reply.")

        # Free text / non-automated patient requests — surface to staff so they can
        # continue manually.
        # The reply is already persisted above; here we alert staff via the existing
        # in-app + SSE notification path (Celery, so the webhook stays fast).
        if parsed_reply.requires_handoff:
            try:
                from src.app.tasks.in_app_notifications import enqueue_in_app_notifications

                enqueue_in_app_notifications(
                    call_id="",
                    institution_id=str(location.institution_id),
                    location_id=str(location.id),
                    call_status=None,
                    call_tags_csv=None,
                    title="Patient campaign response",
                    message=f"A patient SMS reply at {location.name} needs staff review.",
                    notification_type=NotificationType.INBOUND_SMS_REPLY.value,
                    data={
                        "inbound_sms_message_id": str(_inbound_msg.id),
                        "contact_id": _inbound_msg.contact_id,
                        "workflow_run_id": _inbound_msg.workflow_run_id,
                        "campaign_staff_handoff_id": str(_handoff.id) if _handoff else None,
                        "patient_response_intent": parsed_reply.intent,
                    },
                )
            except Exception as notif_err:  # noqa: BLE001 — never fail the webhook on notify
                logger.error(
                    "Failed to enqueue inbound-SMS staff notification: location_hash=%s error=%s",
                    hash_for_logging(str(location.id)),
                    safe_error_summary(notif_err),
                )

        if parsed_reply.requires_handoff:
            logger.info(
                "Inbound SMS staff handoff: from_hash=%s to_hash=%s location_hash=%s intent=%s persisted=%s",
                hash_for_logging(from_number),
                hash_for_logging(to_number),
                hash_for_logging(str(location.id)),
                parsed_reply.intent,
                _inbound_msg.id,
            )
        else:
            logger.info(
                "Inbound SMS response event: from_hash=%s to_hash=%s location_hash=%s intent=%s persisted=%s",
                hash_for_logging(from_number),
                hash_for_logging(to_number),
                hash_for_logging(str(location.id)),
                parsed_reply.intent,
                _inbound_msg.id,
            )
        await session.commit()
        return _twiml("")


@router.post("/sms-status")
async def sms_status(request: Request) -> dict[str, str]:
    form = await _verified_form(request)
    message_sid = _field(form, "MessageSid")
    provider_status = _field(form, "MessageStatus") or _field(form, "SmsStatus")
    provider_error = _field(form, "ErrorMessage") or _field(form, "ErrorCode")
    if not message_sid or not provider_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Twilio status fields",
        )

    async with get_system_db_session(
        "twilio_status",
        external_id=message_sid,
    ) as session:
        sms_service = SmsService(session)
        row = await sms_service.update_delivery_status(
            message_sid=message_sid,
            provider_status=provider_status,
            provider_error=provider_error,
        )
        if not row:
            await capture_dead_letter(
                source="twilio_webhook",
                event_type="sms_status_unmatched_message",
                error="Status callback did not match an SMS history row",
                payload=form,
            )
            await session.commit()
            return {"status": "ignored", "reason": "unknown_message_sid"}
        await session.commit()

    # Meter billable consumption once the message reaches a terminal state.
    # Recorded from a dedicated session because this webhook's RLS context is
    # not authorized for usage_events. Keyed on MessageSid alone so the
    # "sent" then "delivered" callback pair counts a single message once.
    if provider_status.lower().strip() in _TERMINAL_SMS_STATUSES and row.institution_id:
        await record_usage_event(
            institution_id=str(row.institution_id),
            location_id=str(row.location_id) if row.location_id else None,
            channel="sms",
            direction="outbound",
            provider="twilio",
            segments=parse_segments(_field(form, "NumSegments")),
            cost_amount=parse_cost_amount(_field(form, "Price")),
            currency=(_field(form, "PriceUnit") or "USD"),
            provider_message_id=message_sid,
            idempotency_key=f"sms:{message_sid}",
            workflow_run_id=str(row.workflow_run_id) if row.workflow_run_id else None,
            workflow_id=str(row.workflow_id) if row.workflow_id else None,
        )
    return {"status": "updated"}


async def _verified_form(request: Request) -> dict[str, Any]:
    form_data = await request.form()
    form = {str(k): str(v) for k, v in form_data.multi_items()}

    if not settings.twillio_api_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio auth token is not configured",
        )

    # Twilio signs each webhook with the auth token of the (sub-)account that
    # owns the number involved — the destination (To) for inbound SMS, the
    # sender (From) for outbound status callbacks. Resolve the sub-account token
    # for whichever candidate maps to a provisioned location; fall back to the
    # platform token when the number belongs to no sub-account (behavior
    # unchanged for tenants without sub-account credentials).
    async with get_system_db_session(
        "twilio_signature", external_id=_field(form, "To")
    ) as session:
        auth_token = await TenantTwilioCredentialResolver(session).resolve_auth_token(
            _field(form, "To"), _field(form, "From")
        )

    signature = request.headers.get("X-Twilio-Signature")
    validator = RequestValidator(auth_token or settings.twillio_api_secret)
    if not signature or not validator.validate(str(request.url), form, signature):
        logger.warning(
            "Invalid Twilio webhook signature: payload=%s", redact_payload(form)
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Twilio signature"
        )
    return form


async def _location_for_twilio_number(
    session, number: str | None
) -> InstitutionLocation | None:
    if not number:
        return None
    return (
        (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.twilio_from_number == number,
                    InstitutionLocation.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )


async def _audit_keyword(
    location: InstitutionLocation,
    from_number: str | None,
    action: AuditAction,
    keyword: str,
) -> None:
    await log_audit(
        actor=AuditActor.API_CLIENT,
        action=action,
        target_resource=f"sms_phone:{hash_for_logging(from_number)}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "source": "twilio_inbound_sms",
            "keyword": keyword,
            "phone_hash": hash_for_logging(from_number),
            "location_id": str(location.id),
        },
        institution_id=location.institution_id,
        location_id=str(location.id),
    )


def _field(form: dict[str, Any], key: str) -> str | None:
    value = form.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _help_text(location: InstitutionLocation) -> str:
    contact = location.phone or location.twilio_from_number or "the clinic"
    return f"{location.name}: For help, contact {contact}. Reply STOP to opt out."


def _twiml(message: str) -> Response:
    escaped = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if message
        else ""
    )
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>'
    if escaped:
        body += f"<Message>{escaped}</Message>"
    body += "</Response>"
    return Response(content=body, media_type="application/xml")
