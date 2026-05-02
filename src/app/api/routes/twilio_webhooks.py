"""Public Twilio webhooks for inbound SMS keywords and status callbacks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from src.app.config import settings
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_consent import ConsentSource
from src.app.services.audit import log_audit
from src.app.services.dead_letter import capture_dead_letter
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.sms_privacy import hash_for_logging, redact_payload
from src.app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio/webhooks", tags=["Twilio Webhooks"])

STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_KEYWORDS = {"START", "UNSTOP"}
HELP_KEYWORDS = {"HELP", "INFO"}


@router.post("/inbound-sms")
async def inbound_sms(request: Request) -> Response:
    form = await _verified_form(request)
    from_number = _field(form, "From")
    to_number = _field(form, "To")
    body = (_field(form, "Body") or "").strip()
    keyword = body.upper().split()[0] if body else ""

    async with get_db_session() as session:
        location = await _location_for_twilio_number(session, to_number)
        if not location:
            await capture_dead_letter(
                source="twilio_webhook",
                event_type="inbound_sms_unmatched_location",
                error="Inbound SMS could not be mapped to a location",
                payload=form,
            )
            return _twiml("")

        compliance = SmsComplianceService(session)
        if keyword in STOP_KEYWORDS:
            await compliance.suppress(
                institution_id=location.institution_id,
                location_id=str(location.id),
                phone=from_number,
                source=ConsentSource.TWILIO_KEYWORD,
                keyword=keyword,
                reason=f"Twilio inbound keyword: {keyword}",
            )
            await _audit_keyword(location, from_number, AuditAction.SMS_SUPPRESSION_CREATE, keyword)
            await session.commit()
            return _twiml(f"You have been opted out of SMS from {location.name}. Reply START to opt back in.")

        if keyword in START_KEYWORDS:
            await compliance.release_suppression(
                institution_id=location.institution_id,
                location_id=str(location.id),
                phone=from_number,
                source=ConsentSource.TWILIO_KEYWORD,
                reason=f"Twilio inbound keyword: {keyword}",
            )
            await _audit_keyword(location, from_number, AuditAction.SMS_SUPPRESSION_RELEASE, keyword)
            await session.commit()
            return _twiml(f"You have been opted in to SMS from {location.name}. Reply STOP to opt out.")

        if keyword in HELP_KEYWORDS:
            await session.commit()
            return _twiml(_help_text(location))

        logger.info(
            "Inbound SMS ignored: from=%s to=%s location=%s keyword=%s",
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Twilio status fields")

    async with get_db_session() as session:
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
        return {"status": "updated"}


async def _verified_form(request: Request) -> dict[str, Any]:
    form_data = await request.form()
    form = {str(k): str(v) for k, v in form_data.multi_items()}

    if not settings.twillio_api_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Twilio auth token is not configured")

    signature = request.headers.get("X-Twilio-Signature")
    validator = RequestValidator(settings.twillio_api_secret)
    if not signature or not validator.validate(str(request.url), form, signature):
        logger.warning("Invalid Twilio webhook signature: payload=%s", redact_payload(form))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Twilio signature")
    return form


async def _location_for_twilio_number(session, number: str | None) -> InstitutionLocation | None:
    if not number:
        return None
    return (
        await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.twilio_from_number == number,
                InstitutionLocation.is_active.is_(True),
            )
        )
    ).scalars().first()


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
    body = f'<?xml version="1.0" encoding="UTF-8"?><Response>'
    if escaped:
        body += f"<Message>{escaped}</Message>"
    body += "</Response>"
    return Response(content=body, media_type="application/xml")
