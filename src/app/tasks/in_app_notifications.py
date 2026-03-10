"""Background tasks for in-app notifications."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.app.config import settings
from src.app.database import get_db_session, init_database, is_database_initialized
from src.app.models.call import Call, CallStatus
from src.app.models.notification import NotificationType
from src.app.services.notification_service import NotificationService
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


_URGENT_TAGS = frozenset({CallStatus.EMERGENCY.value, CallStatus.COMPLAINT.value})


def _is_urgent(primary_tag: str | None, tags: list[str]) -> bool:
    if primary_tag in _URGENT_TAGS:
        return True
    return any(tag in _URGENT_TAGS for tag in tags)


async def _send_in_app_notifications_async(
    *,
    call_id: str,
    institution_id: str,
    location_id: str | None,
    call_status: str | None,
    call_tags_csv: str | None,
    title: str | None,
    message: str | None,
    notification_type: str | None,
    data: dict[str, Any] | None,
) -> None:
    """Core async logic for creating in-app notifications."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to send in-app notifications")
    if not is_database_initialized():
        init_database(settings.database_url)

    async with get_db_session() as session:
        # If call_id is provided, load the call and use service method
        if call_id:
            call = (
                await session.execute(
                    select(Call)
                    .where(Call.id == call_id, Call.institution_id == institution_id)
                    .options(selectinload(Call.contact))
                )
            ).scalar_one_or_none()

            if call:
                svc = NotificationService(session)
                count = await svc.create_notifications_for_call(
                    institution_id=institution_id,
                    location_id=location_id,
                    call=call,
                    call_status=call_status,
                    call_tags_csv=call_tags_csv,
                )
                logger.info(
                    "In-app notifications created via call: call=%s count=%d institution=%s",
                    call_id,
                    count,
                    institution_id,
                )
                return

            logger.warning(
                "Call not found for in-app notification: call_id=%s institution=%s",
                call_id,
                institution_id,
            )

        # Fallback: create bulk notifications using provided title/message
        if title and message and notification_type:
            svc = NotificationService(session)
            count = await svc.create_bulk_notifications(
                institution_id=institution_id,
                location_id=location_id,
                notification_type=notification_type,
                title=title,
                message=message,
                data=data,
            )
            logger.info(
                "Bulk in-app notifications created: type=%s count=%d institution=%s",
                notification_type,
                count,
                institution_id,
            )
        else:
            logger.warning(
                "Insufficient data for in-app notification: call_id=%s title=%s",
                call_id,
                title,
            )


@celery_app.task(
    name="src.app.tasks.in_app_notifications.send_in_app_notifications",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def send_in_app_notifications(
    self,  # noqa: ARG001 - required for bind=True retries
    call_id: str | None = None,
    institution_id: str = "",
    location_id: str | None = None,
    call_status: str | None = None,
    call_tags_csv: str | None = None,
    title: str | None = None,
    message: str | None = None,
    notification_type: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Celery task that creates in-app notifications."""
    asyncio.run(
        _send_in_app_notifications_async(
            call_id=call_id or "",
            institution_id=institution_id,
            location_id=location_id,
            call_status=call_status,
            call_tags_csv=call_tags_csv,
            title=title,
            message=message,
            notification_type=notification_type,
            data=data,
        )
    )


def enqueue_in_app_notifications(
    *,
    call_id: str,
    institution_id: str,
    location_id: str | None,
    call_status: str | None,
    call_tags_csv: str | None,
    title: str | None = None,
    message: str | None = None,
    notification_type: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Queue an in-app notification background task."""
    if not settings.celery_broker_url:
        logger.warning("CELERY_BROKER_URL is not set. Skipping in-app notification enqueue.")
        return

    tags = _split_csv(call_tags_csv)
    queue_name = "notifications_high" if _is_urgent(call_status, tags) else "notifications_default"

    send_in_app_notifications.apply_async(
        kwargs={
            "call_id": call_id,
            "institution_id": institution_id,
            "location_id": location_id,
            "call_status": call_status,
            "call_tags_csv": call_tags_csv,
            "title": title,
            "message": message,
            "notification_type": notification_type,
            "data": data or {},
        },
        queue=queue_name,
    )
