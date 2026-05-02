"""Background SMS tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.app.config import settings
from src.app.database import get_db_session, init_database, is_database_initialized
from src.app.models.sms_history_log import SmsStatus
from src.app.services.dead_letter import capture_dead_letter, should_retry_vendor_error
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
) -> dict[str, Any]:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to process SMS tasks")
    if not is_database_initialized():
        init_database(settings.database_url)

    async with get_db_session() as session:
        sms_service = SmsService(session)
        log_record = await sms_service.send_sms(
            from_number=from_number,
            to_number=to_number,
            body=body,
            institution_location_id=institution_location_id,
            patient_contact_id=patient_contact_id,
            call_id=call_id,
        )
        await session.commit()

        result = {
            "status": log_record.status,
            "provider_status": log_record.provider_status,
            "error_message": log_record.error_message,
            "message_sid": log_record.message_sid,
            "location_id": institution_location_id,
            "call_id": call_id,
        }

        if log_record.status == SmsStatus.SENT.value:
            logger.info(
                "SMS task sent: sid=%s location=%s call=%s",
                log_record.message_sid,
                institution_location_id,
                call_id,
            )
        elif log_record.status == SmsStatus.SUPPRESSED.value:
            logger.info("SMS task suppressed: location=%s call=%s", institution_location_id, call_id)
        return result


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
) -> None:
    payload = {
        "from_number": from_number,
        "to_number": to_number,
        "body": body,
        "institution_location_id": institution_location_id,
        "patient_contact_id": patient_contact_id,
        "call_id": call_id,
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
            )
        )
    except Exception as exc:
        _handle_sms_task_failure(self, exc=exc, payload=payload)
        return

    if result.get("status") != SmsStatus.FAILED.value:
        return

    error_message = result.get("error_message") or "Unknown Twilio failure"
    provider_status = result.get("provider_status") or ""
    retryable = provider_status.startswith("retryable") or should_retry_vendor_error(error_message)
    if retryable and self.request.retries < self.max_retries:
        raise self.retry(exc=RuntimeError(error_message), countdown=_retry_countdown(self.request.retries))

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
) -> None:
    """Queue a call-triggered SMS for worker processing."""
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
        },
        queue="notifications_default",
    )
