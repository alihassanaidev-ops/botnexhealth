"""Public Twilio webhooks for inbound SMS keywords and status callbacks."""

from __future__ import annotations

import logging
import re
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
from src.app.services.sms_privacy import hash_for_logging, redact_payload
from src.app.services.sms_service import SmsService
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

# CASL (Canada) requires honoring French opt-out/help keywords alongside the
# English ones. Entries are stored UPPERCASE (incl. accented forms) because the
# body is uppercased before tokenizing and Python's str.upper() maps é→É, ê→Ê.
STOP_KEYWORDS = {
    "STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT",
    "ARRET", "ARRÊT", "DESABONNER", "DÉSABONNER", "RETIRER", "SUPPRIMER",
}
START_KEYWORDS = {"START", "UNSTOP"}
HELP_KEYWORDS = {"HELP", "INFO", "AIDE"}

# Tokenize on any non-letter run. Catches phrasings like "please stop calling"
# and "STOP!" without false-positives on partial words inside other tokens.
# Unicode letter class (not just A-Z) so accented French keywords like ARRÊT
# and DÉSABONNER survive tokenization instead of splitting around the accent.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _classify_intent(body: str) -> str:
    """Return STOP/START/HELP if any keyword appears as a whole token.

    STOP wins over START in the unlikely "STOP and START" case — opting out
    is the safer default. HELP is only returned when no opt-out keyword is
    present.
    """
    tokens = set(_TOKEN_RE.findall(body.upper()))
    if tokens & STOP_KEYWORDS:
        return "STOP"
    if tokens & START_KEYWORDS:
        return "START"
    if tokens & HELP_KEYWORDS:
        return "HELP"
    return ""


@router.post("/inbound-sms")
async def inbound_sms(request: Request) -> Response:
    form = await _verified_form(request)
    from_number = _field(form, "From")
    to_number = _field(form, "To")
    body = (_field(form, "Body") or "").strip()
    intent = _classify_intent(body)
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

        logger.info(
            "Inbound SMS ignored: from_hash=%s to_hash=%s location_hash=%s keyword=%s",
            hash_for_logging(from_number),
            hash_for_logging(to_number),
            hash_for_logging(str(location.id)),
            keyword or "none",
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
