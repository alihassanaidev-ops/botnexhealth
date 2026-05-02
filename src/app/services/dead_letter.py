"""Dead-letter capture and operator actions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.database import get_db_session, init_database, is_database_initialized
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.services.sms_privacy import payload_hash, redact_payload, sanitize_provider_error

logger = logging.getLogger(__name__)


class DeadLetterService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def capture(
        self,
        *,
        source: str,
        event_type: str,
        error: Exception | str,
        payload: Any,
        raw_payload: str | None = None,
        attempts: int = 1,
        institution_id: str | None = None,
        location_id: str | None = None,
    ) -> DeadLetterEvent:
        redacted = redact_payload(payload)
        if not isinstance(redacted, dict):
            redacted = {"payload": redacted}
        row = DeadLetterEvent(
            source=source,
            event_type=event_type,
            attempts=attempts,
            last_error=sanitize_provider_error(error),
            payload_hash=payload_hash(payload),
            redacted_payload=redacted,
            institution_id=institution_id,
            location_id=location_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        row.raw_payload = raw_payload if raw_payload is not None else _json_dumps(payload)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_open(self, event_id: str) -> DeadLetterEvent | None:
        return (
            await self.session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.id == event_id,
                    DeadLetterEvent.status == DeadLetterStatus.OPEN.value,
                )
            )
        ).scalar_one_or_none()

    async def mark_discarded(self, row: DeadLetterEvent, *, user_id: str | None) -> None:
        row.status = DeadLetterStatus.DISCARDED.value
        row.resolved_by_user_id = user_id
        row.resolved_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    async def mark_replayed(self, row: DeadLetterEvent, *, user_id: str | None) -> None:
        row.status = DeadLetterStatus.REPLAYED.value
        row.resolved_by_user_id = user_id
        row.resolved_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)


async def capture_dead_letter(
    *,
    source: str,
    event_type: str,
    error: Exception | str,
    payload: Any,
    raw_payload: str | None = None,
    attempts: int = 1,
    institution_id: str | None = None,
    location_id: str | None = None,
) -> None:
    """Best-effort DLQ capture that can be called from tasks/webhooks."""
    try:
        if not settings.database_url:
            logger.warning("Skipping DLQ capture because DATABASE_URL is not configured")
            return
        if not is_database_initialized():
            init_database(settings.database_url)
        async with get_db_session() as session:
            svc = DeadLetterService(session)
            await svc.capture(
                source=source,
                event_type=event_type,
                error=error,
                payload=payload,
                raw_payload=raw_payload,
                attempts=attempts,
                institution_id=institution_id,
                location_id=location_id,
            )
            await session.commit()
    except Exception:
        logger.warning("Failed to capture dead-letter event", exc_info=True)


def should_retry_vendor_error(error: Exception | str) -> bool:
    """Classify vendor failures for Celery retry decisions."""
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)

    if status_code is not None:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            code = 0
        return code == 429 or code >= 500

    name = type(error).__name__.lower()
    text = str(error).lower()
    retry_markers = ("timeout", "temporarily", "connection", "network", "rate limit", "too many requests")
    non_retry_markers = ("credential", "auth", "forbidden", "invalid", "suppressed", "opted out", "consent")
    if any(marker in text for marker in non_retry_markers):
        return False
    return any(marker in name or marker in text for marker in retry_markers)


def _json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return str(payload)
