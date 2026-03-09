"""Background SMS tasks."""

from __future__ import annotations

import asyncio
import logging

from src.app.config import settings
from src.app.database import get_db_session, init_database, is_database_initialized
from src.app.models.sms_history_log import SmsStatus
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
) -> None:
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

        if log_record.status == SmsStatus.FAILED.value:
            raise RuntimeError(log_record.error_message or "Unknown Twilio failure")

        logger.info(
            "SMS task sent: sid=%s location=%s call=%s",
            log_record.message_sid,
            institution_location_id,
            call_id,
        )


@celery_app.task(
    name="src.app.tasks.sms.send_sms_message",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_sms_message(
    self,  # noqa: ARG001 - required for bind=True retries
    from_number: str,
    to_number: str,
    body: str,
    institution_location_id: str,
    patient_contact_id: str | None = None,
    call_id: str | None = None,
) -> None:
    asyncio.run(
        _send_sms_async(
            from_number=from_number,
            to_number=to_number,
            body=body,
            institution_location_id=institution_location_id,
            patient_contact_id=patient_contact_id,
            call_id=call_id,
        )
    )


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

