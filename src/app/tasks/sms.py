"""Background SMS tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from src.app.config import settings
from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_history_log import SmsStatus
from src.app.services.dead_letter import capture_dead_letter, should_retry_vendor_error
from src.app.services.sms_privacy import hash_for_logging
from src.app.services.sms_service import SmsService
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


async def _send_sms_async(
    *,
    from_number: str,
    to_number: str,
    body: str,
    institution_location_id: str,
    patient_contact_id: str | None,
    call_id: str | None,
    include_opt_out_footer: bool = True,
    include_clinic_identity: bool = True,
) -> dict[str, Any]:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to process SMS tasks")
    if not is_database_initialized():
        init_database(settings.database_url)

    institution_id = await _resolve_institution_id_for_location(institution_location_id)
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=institution_location_id,
        external_id=institution_location_id,
    ) as session:
        sms_service = SmsService(session)
        log_record = await sms_service.send_sms(
            from_number=from_number,
            to_number=to_number,
            body=body,
            institution_location_id=institution_location_id,
            patient_contact_id=patient_contact_id,
            call_id=call_id,
            include_opt_out_footer=include_opt_out_footer,
            include_clinic_identity=include_clinic_identity,
        )
        await session.commit()

        result = {
            "status": log_record.status,
            "provider_status": log_record.provider_status,
            "error_message": log_record.error_message,
            "message_sid": log_record.message_sid,
            "institution_id": institution_id,
            "location_id": institution_location_id,
            "call_id": call_id,
        }

        if log_record.status == SmsStatus.SENT.value:
            logger.info(
                "SMS task sent: sid_hash=%s location_hash=%s call_hash=%s",
                hash_for_logging(log_record.message_sid),
                hash_for_logging(institution_location_id),
                hash_for_logging(call_id),
            )
        elif log_record.status == SmsStatus.SUPPRESSED.value:
            logger.info(
                "SMS task suppressed: location_hash=%s call_hash=%s",
                hash_for_logging(institution_location_id),
                hash_for_logging(call_id),
            )
        return result


async def _resolve_institution_id_for_location(institution_location_id: str) -> str:
    """Resolve the location's institution before opening the PHI send session."""
    async with get_system_db_session(
        "celery",
        location_id=institution_location_id,
        external_id=institution_location_id,
    ) as session:
        institution_id = (
            await session.execute(
                select(InstitutionLocation.institution_id).where(
                    InstitutionLocation.id == institution_location_id
                )
            )
        ).scalar_one_or_none()

    if not institution_id:
        raise ValueError("Institution location not found for SMS send")
    return str(institution_id)


@celery_app.task(
    name="src.app.tasks.sms.send_sms_message",
    bind=True,
    max_retries=5,
)
def send_sms_message(
    self,
    from_number: str,
    to_number: str,
    body: str,
    institution_location_id: str,
    patient_contact_id: str | None = None,
    call_id: str | None = None,
    include_opt_out_footer: bool = True,
    include_clinic_identity: bool = True,
) -> None:
    payload = {
        "from_number": from_number,
        "to_number": to_number,
        "body": body,
        "institution_location_id": institution_location_id,
        "patient_contact_id": patient_contact_id,
        "call_id": call_id,
        "include_opt_out_footer": include_opt_out_footer,
        "include_clinic_identity": include_clinic_identity,
    }
    try:
        result = asyncio.run(
            _send_sms_async(
                from_number=from_number,
                to_number=to_number,
                body=body,
                institution_location_id=institution_location_id,
                patient_contact_id=patient_contact_id,
                call_id=call_id,
                include_opt_out_footer=include_opt_out_footer,
                include_clinic_identity=include_clinic_identity,
            )
        )
    except Exception as exc:
        _handle_sms_task_failure(self, exc=exc, payload=payload)
        return

    if result.get("status") != SmsStatus.FAILED.value:
        return

    error_message = result.get("error_message") or "Unknown Twilio failure"
    provider_status = result.get("provider_status") or ""
    retryable = provider_status.startswith("retryable") or should_retry_vendor_error(
        error_message
    )
    if retryable and self.request.retries < self.max_retries:
        raise self.retry(
            exc=RuntimeError(error_message),
            countdown=_retry_countdown(self.request.retries),
        )

    asyncio.run(
        capture_dead_letter(
            source="sms_task",
            event_type="send_sms_message",
            error=error_message,
            payload=payload,
            attempts=self.request.retries + 1,
            location_id=institution_location_id,
        )
    )


def _handle_sms_task_failure(self, *, exc: Exception, payload: dict[str, Any]) -> None:
    retryable = should_retry_vendor_error(exc)
    if retryable and self.request.retries < self.max_retries:
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))
    asyncio.run(
        capture_dead_letter(
            source="sms_task",
            event_type="send_sms_message",
            error=exc,
            payload=payload,
            attempts=self.request.retries + 1,
            location_id=payload.get("institution_location_id"),
        )
    )


def _retry_countdown(retries: int) -> int:
    return min(300, 2 ** max(retries, 0))


def enqueue_auto_sms(
    *,
    from_number: str,
    to_number: str,
    body: str,
    institution_location_id: str,
    patient_contact_id: str | None = None,
    call_id: str | None = None,
    include_opt_out_footer: bool = True,
    include_clinic_identity: bool = True,
) -> None:
    """Queue a call-triggered SMS for worker processing.

    Both flags are False for the no-PMS sends whose wording is owned end-to-end
    by the institution admin's editable SMS templates: what the admin saves is
    exactly what the recipient receives, with neither a location-name prefix in
    front of it nor opt-out copy appended underneath.

    They stay True for the PMS ``appointment_booked`` confirmation, which keeps
    the identity prefix and CASL footer it has always carried — that clinic has
    no access to the SMS template editor to supply its own.
    """
    if not settings.celery_broker_url:
        raise RuntimeError("CELERY_BROKER_URL is not set")

    send_sms_message.apply_async(
        kwargs={
            "from_number": from_number,
            "to_number": to_number,
            "body": body,
            "institution_location_id": institution_location_id,
            "patient_contact_id": patient_contact_id,
            "call_id": call_id,
            "include_opt_out_footer": include_opt_out_footer,
            "include_clinic_identity": include_clinic_identity,
        },
        queue="notifications_default",
    )
