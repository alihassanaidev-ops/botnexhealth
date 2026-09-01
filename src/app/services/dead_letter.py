"""Dead-letter capture and operator actions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.models.institution_location import InstitutionLocation
from src.app.services.retention_policy import default_dead_letter_raw_retain_until
from src.app.services.sms_privacy import (
    payload_hash,
    redact_payload,
    safe_error_summary,
    sanitize_provider_error,
)

logger = logging.getLogger(__name__)

_AUTO_RESOLVED_REASON = "resolved_elsewhere"


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
        now = datetime.now(timezone.utc)
        fingerprint = payload_hash(payload)
        existing = await self._open_duplicate_for_update(
            source=source,
            event_type=event_type,
            payload_fingerprint=fingerprint,
            institution_id=institution_id,
            location_id=location_id,
        )
        if existing is not None:
            existing.attempts = max(existing.attempts, attempts)
            existing.last_error = sanitize_provider_error(error)
            existing.redacted_payload = redacted
            existing.updated_at = now
            if existing.raw_payload_encrypted is None or raw_payload is not None:
                existing.raw_payload = (
                    raw_payload if raw_payload is not None else _json_dumps(payload)
                )
            await self.session.flush()
            return existing

        row = DeadLetterEvent(
            source=source,
            event_type=event_type,
            attempts=attempts,
            last_error=sanitize_provider_error(error),
            payload_hash=fingerprint,
            redacted_payload=redacted,
            institution_id=institution_id,
            location_id=location_id,
            created_at=now,
            updated_at=now,
            raw_payload_retain_until=default_dead_letter_raw_retain_until(now),
        )
        row.raw_payload = (
            raw_payload if raw_payload is not None else _json_dumps(payload)
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _open_duplicate_for_update(
        self,
        *,
        source: str,
        event_type: str,
        payload_fingerprint: str,
        institution_id: str | None,
        location_id: str | None,
    ) -> DeadLetterEvent | None:
        return (
            await self.session.execute(
                select(DeadLetterEvent)
                .where(
                    DeadLetterEvent.source == source,
                    DeadLetterEvent.event_type == event_type,
                    DeadLetterEvent.status == DeadLetterStatus.OPEN.value,
                    DeadLetterEvent.payload_hash == payload_fingerprint,
                    _scope_clause(DeadLetterEvent.institution_id, institution_id),
                    _scope_clause(DeadLetterEvent.location_id, location_id),
                )
                .order_by(DeadLetterEvent.created_at.asc())
                .with_for_update()
                .limit(1)
            )
        ).scalar_one_or_none()

    async def get_open(self, event_id: str) -> DeadLetterEvent | None:
        return (
            await self.session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.id == event_id,
                    DeadLetterEvent.status == DeadLetterStatus.OPEN.value,
                )
            )
        ).scalar_one_or_none()

    async def get_for_update(self, event_id: str) -> DeadLetterEvent | None:
        """Lock one event while an operator resolves it.

        Replay reaches an external system. Holding the row lock across the
        enqueue makes two concurrent button clicks serialize: the second sees
        the first request's terminal status and cannot enqueue the payload a
        second time.
        """
        return (
            await self.session.execute(
                select(DeadLetterEvent)
                .where(DeadLetterEvent.id == event_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_open_matching_for_update(
        self,
        row: DeadLetterEvent,
    ) -> list[DeadLetterEvent]:
        """Lock open rows that represent the same captured work item."""
        return (
            await self.session.execute(
                select(DeadLetterEvent)
                .where(
                    DeadLetterEvent.source == row.source,
                    DeadLetterEvent.event_type == row.event_type,
                    DeadLetterEvent.status == DeadLetterStatus.OPEN.value,
                    DeadLetterEvent.payload_hash == row.payload_hash,
                    _scope_clause(DeadLetterEvent.institution_id, row.institution_id),
                    _scope_clause(DeadLetterEvent.location_id, row.location_id),
                )
                .order_by(DeadLetterEvent.created_at.asc())
                .with_for_update()
            )
        ).scalars().all()

    async def mark_discarded(
        self,
        row: DeadLetterEvent,
        *,
        user_id: str | None,
        reason: str,
        note: str | None = None,
    ) -> None:
        row.status = DeadLetterStatus.DISCARDED.value
        row.resolved_by_user_id = user_id
        row.resolution_reason = reason
        row.resolution_note = note
        row.resolved_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    async def mark_replayed(self, row: DeadLetterEvent, *, user_id: str | None) -> None:
        row.status = DeadLetterStatus.REPLAYED.value
        row.resolved_by_user_id = user_id
        row.resolved_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    async def mark_workflow_timer_succeeded(
        self,
        *,
        timer_id: str,
        run_id: str,
        institution_id: str,
        location_id: str | None,
    ) -> int:
        """Resolve open dead-letter alerts for a workflow timer that later ran."""
        fingerprint = payload_hash({"timer_id": timer_id, "run_id": run_id})
        rows = (
            await self.session.execute(
                select(DeadLetterEvent)
                .where(
                    DeadLetterEvent.source == "workflow_dispatch",
                    DeadLetterEvent.event_type == "dispatch_workflow_timer",
                    DeadLetterEvent.status == DeadLetterStatus.OPEN.value,
                    DeadLetterEvent.payload_hash == fingerprint,
                    _scope_clause(DeadLetterEvent.institution_id, institution_id),
                    _scope_clause(DeadLetterEvent.location_id, location_id),
                )
                .with_for_update()
            )
        ).scalars().all()
        if not rows:
            return 0
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = DeadLetterStatus.DISCARDED.value
            row.resolved_by_user_id = None
            row.resolution_reason = _AUTO_RESOLVED_REASON
            row.resolution_note = None
            row.resolved_at = now
            row.updated_at = now
        await self.session.flush()
        return len(rows)


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
            logger.warning(
                "Skipping DLQ capture because DATABASE_URL is not configured"
            )
            return
        if not is_database_initialized():
            init_database(settings.database_url)
        async with get_system_db_session(
            "dead_letter",
            institution_id=institution_id,
            location_id=location_id,
        ) as session:
            # Some older task call sites knew only their location. A row with a
            # NULL institution_id is invisible to the clinic under the table's
            # RLS policy, which made the operator screen silently omit exactly
            # those failures. Resolve the owner before inserting instead of
            # trusting every future caller to remember both ids.
            if institution_id is None and location_id is not None:
                resolved_institution_id = (
                    await session.execute(
                        select(InstitutionLocation.institution_id).where(
                            InstitutionLocation.id == location_id
                        )
                    )
                ).scalar_one_or_none()
                if resolved_institution_id is not None:
                    institution_id = str(resolved_institution_id)
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
    except Exception as exc:
        logger.warning(
            "Failed to capture dead-letter event: %s", safe_error_summary(exc)
        )


async def resolve_workflow_timer_dead_letters(
    *,
    timer_id: str,
    run_id: str,
    institution_id: str,
    location_id: str | None,
) -> int:
    """Best-effort cleanup when a previously dead-lettered timer succeeds."""
    try:
        if not settings.database_url:
            return 0
        if not is_database_initialized():
            init_database(settings.database_url)
        async with get_system_db_session(
            "dead_letter",
            institution_id=institution_id,
            location_id=location_id,
        ) as session:
            resolved = await DeadLetterService(session).mark_workflow_timer_succeeded(
                timer_id=timer_id,
                run_id=run_id,
                institution_id=institution_id,
                location_id=location_id,
            )
            await session.commit()
            if resolved:
                logger.info(
                    "Resolved %d dead-letter alert(s) for workflow timer=%s run=%s",
                    resolved,
                    timer_id,
                    run_id,
                )
            return resolved
    except Exception as exc:
        logger.warning(
            "Failed to resolve workflow timer dead-letter alerts: %s",
            safe_error_summary(exc),
        )
        return 0


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
    retry_markers = (
        "timeout",
        "temporarily",
        "connection",
        "network",
        "rate limit",
        "too many requests",
    )
    non_retry_markers = (
        "credential",
        "auth",
        "forbidden",
        "invalid",
        "suppressed",
        "opted out",
        "consent",
    )
    if any(marker in text for marker in non_retry_markers):
        return False
    return any(marker in name or marker in text for marker in retry_markers)


def _json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return str(payload)


def _scope_clause(column, value: str | None):  # noqa: ANN001
    return column.is_(None) if value is None else column == value
