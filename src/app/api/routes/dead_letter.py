"""Operator APIs for dead-letter events."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from src.app.api.deps import get_current_admin
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.models.user import User
from src.app.services.audit import log_audit
from src.app.services.dead_letter import DeadLetterService

router = APIRouter(prefix="/admin/dead-letter-events", tags=["Admin - Dead Letter Events"])


class DeadLetterResponse(BaseModel):
    id: str
    source: str
    event_type: str
    status: str
    attempts: int
    last_error: str
    payload_hash: str
    redacted_payload: dict[str, Any] | None
    institution_id: str | None
    location_id: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class DeadLetterListResponse(BaseModel):
    items: list[DeadLetterResponse]
    total: int
    page: int
    size: int
    pages: int


@router.get("", response_model=DeadLetterListResponse)
async def list_dead_letter_events(
    _: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status_filter: str = Query(DeadLetterStatus.OPEN.value, alias="status"),
    source: str | None = None,
) -> DeadLetterListResponse:
    async with get_db_session() as session:
        filters = []
        if status_filter:
            filters.append(DeadLetterEvent.status == status_filter)
        if source:
            filters.append(DeadLetterEvent.source == source)

        stmt = select(DeadLetterEvent)
        count_stmt = select(func.count()).select_from(DeadLetterEvent)
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = int((await session.execute(count_stmt)).scalar() or 0)
        rows = (
            await session.execute(
                stmt.order_by(DeadLetterEvent.created_at.desc()).offset((page - 1) * size).limit(size)
            )
        ).scalars().all()
        return DeadLetterListResponse(
            items=[_response(row) for row in rows],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if total else 0,
        )


@router.post("/{event_id}/discard", response_model=DeadLetterResponse)
async def discard_dead_letter_event(
    event_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DeadLetterResponse:
    async with get_db_session() as session:
        svc = DeadLetterService(session)
        row = await svc.get_open(event_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Open dead-letter event not found")
        await svc.mark_discarded(row, user_id=str(current_admin.id))
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.DEAD_LETTER_DISCARD,
            target_resource=f"dead_letter:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"source": row.source, "event_type": row.event_type},
            institution_id=str(row.institution_id) if row.institution_id else None,
            user_id=str(current_admin.id),
            location_id=str(row.location_id) if row.location_id else None,
        )
        await session.commit()
        return _response(row)


@router.post("/{event_id}/replay", response_model=DeadLetterResponse)
async def replay_dead_letter_event(
    event_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DeadLetterResponse:
    async with get_db_session() as session:
        svc = DeadLetterService(session)
        row = await svc.get_open(event_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Open dead-letter event not found")

        await _replay(row)
        await svc.mark_replayed(row, user_id=str(current_admin.id))
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.DEAD_LETTER_REPLAY,
            target_resource=f"dead_letter:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"source": row.source, "event_type": row.event_type},
            institution_id=str(row.institution_id) if row.institution_id else None,
            user_id=str(current_admin.id),
            location_id=str(row.location_id) if row.location_id else None,
        )
        await session.commit()
        return _response(row)


async def _replay(row: DeadLetterEvent) -> None:
    raw = row.raw_payload
    payload = _raw_payload(row)
    if row.source == "sms_task" and row.event_type == "send_sms_message":
        from src.app.tasks.sms import send_sms_message

        send_sms_message.apply_async(kwargs=payload, queue="notifications_default")
        return
    if row.source == "notification_task" and row.event_type == "send_call_notification":
        from src.app.tasks.notifications import send_call_notification

        send_call_notification.apply_async(kwargs=payload, queue="notifications_default")
        return
    if row.source == "retell_webhook" and raw:
        from src.app.retell.webhooks import handle_retell_webhook

        await handle_retell_webhook(body=raw.encode("utf-8"))
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Replay is not supported for {row.source}:{row.event_type}",
    )


def _raw_payload(row: DeadLetterEvent) -> dict[str, Any]:
    raw = row.raw_payload
    if not raw:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No replay payload is available")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Replay payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Replay payload must be an object")
    return payload


def _response(row: DeadLetterEvent) -> DeadLetterResponse:
    return DeadLetterResponse(
        id=str(row.id),
        source=row.source,
        event_type=row.event_type,
        status=row.status,
        attempts=row.attempts,
        last_error=row.last_error,
        payload_hash=row.payload_hash,
        redacted_payload=row.redacted_payload,
        institution_id=str(row.institution_id) if row.institution_id else None,
        location_id=str(row.location_id) if row.location_id else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )
