"""Operator APIs for dead-letter events."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.app.api.deps import get_current_admin
from src.app.api.permissions import Permission, require_permission
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.models.user import User
from src.app.services.audit import log_audit
from src.app.services.dead_letter import DeadLetterService

router = APIRouter(prefix="/admin/dead-letter-events", tags=["Admin - Dead Letter Events"])


class DismissalReason(str, Enum):
    RESOLVED_ELSEWHERE = "resolved_elsewhere"
    DUPLICATE = "duplicate"
    NOT_ACTIONABLE = "not_actionable"
    SUPERSEDED = "superseded"
    OTHER = "other"


class DiscardDeadLetterRequest(BaseModel):
    reason: DismissalReason
    # Free text may contain PHI. It is encrypted on the model and deliberately
    # excluded from audit metadata.
    note: str | None = Field(default=None, max_length=1000)


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
    resolution_reason: str | None
    resolution_note: str | None
    replay_supported: bool
    originating_run_id: str | None
    originating_timer_id: str | None


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
    return await _list_dead_letter_events(
        page=page,
        size=size,
        status_filter=status_filter,
        source=source,
        include_resolution_note=False,
    )


async def _list_dead_letter_events(
    *,
    page: int,
    size: int,
    status_filter: str,
    source: str | None,
    include_resolution_note: bool,
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
            items=[
                _response(row, include_resolution_note=include_resolution_note)
                for row in rows
            ],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if total else 0,
        )


@router.post("/{event_id}/discard", response_model=DeadLetterResponse)
async def discard_dead_letter_event(
    event_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
    request: DiscardDeadLetterRequest,
) -> DeadLetterResponse:
    return await _discard_dead_letter_event(
        event_id=event_id,
        current_user=current_admin,
        request=request,
        include_resolution_note=False,
    )


async def _discard_dead_letter_event(
    *,
    event_id: str,
    current_user: User,
    request: DiscardDeadLetterRequest,
    include_resolution_note: bool,
) -> DeadLetterResponse:
    async with get_db_session() as session:
        svc = DeadLetterService(session)
        row = await svc.get_for_update(event_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation issue not found")
        if row.status == DeadLetterStatus.DISCARDED.value:
            # A repeated request is a no-op. This also makes a browser retry
            # after a lost response converge on the state already written.
            return _response(row, include_resolution_note=include_resolution_note)
        if row.status != DeadLetterStatus.OPEN.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an open automation issue can be marked resolved",
            )
        note = request.note.strip() if request.note and request.note.strip() else None
        matching_rows = await svc.get_open_matching_for_update(row)
        if not matching_rows:
            matching_rows = [row]
        for matching_row in matching_rows:
            await svc.mark_discarded(
                matching_row,
                user_id=str(current_user.id),
                reason=request.reason.value,
                note=note,
            )
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.DEAD_LETTER_DISCARD,
            target_resource=f"dead_letter:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "source": row.source,
                "event_type": row.event_type,
                "reason": request.reason.value,
                "note_recorded": note is not None,
                "resolved_event_count": len(matching_rows),
            },
            institution_id=str(row.institution_id) if row.institution_id else None,
            user_id=str(current_user.id),
            location_id=str(row.location_id) if row.location_id else None,
        )
        await session.commit()
        return _response(row, include_resolution_note=include_resolution_note)


@router.post(
    "/{event_id}/replay",
    response_model=DeadLetterResponse,
    # Already super-admin only. The permission makes the *reason* explicit and
    # testable: a replay re-runs a write that can reach a practice's records,
    # which is a different kind of risk from ordinary administration.
    dependencies=[Depends(require_permission(Permission.WRITE_REPLAY))],
)
async def replay_dead_letter_event(
    event_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DeadLetterResponse:
    return await _replay_dead_letter_event(
        event_id=event_id,
        current_user=current_admin,
        include_resolution_note=False,
    )


async def _replay_dead_letter_event(
    *,
    event_id: str,
    current_user: User,
    include_resolution_note: bool,
) -> DeadLetterResponse:
    async with get_db_session() as session:
        svc = DeadLetterService(session)
        row = await svc.get_for_update(event_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation issue not found")
        if row.status == DeadLetterStatus.REPLAYED.value:
            # Idempotent response for a repeated click/request. The row lock
            # means a concurrent request gets here only after the first one has
            # committed, so it never enqueues the payload twice.
            return _response(row, include_resolution_note=include_resolution_note)
        if row.status != DeadLetterStatus.OPEN.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an open automation issue can be retried",
            )
        if not _replay_supported(row):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Replay is not supported for {row.source}:{row.event_type}",
            )

        matching_rows = await svc.get_open_matching_for_update(row)
        if not matching_rows:
            matching_rows = [row]
        await _replay(row)
        for matching_row in matching_rows:
            await svc.mark_replayed(matching_row, user_id=str(current_user.id))
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.DEAD_LETTER_REPLAY,
            target_resource=f"dead_letter:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "source": row.source,
                "event_type": row.event_type,
                "resolved_event_count": len(matching_rows),
            },
            institution_id=str(row.institution_id) if row.institution_id else None,
            user_id=str(current_user.id),
            location_id=str(row.location_id) if row.location_id else None,
        )
        await session.commit()
        return _response(row, include_resolution_note=include_resolution_note)


async def _replay(row: DeadLetterEvent) -> None:
    raw = row.raw_payload
    if row.source == "sms_task" and row.event_type == "send_sms_message":
        payload = _raw_payload(row)
        from src.app.tasks.sms import send_sms_message

        send_sms_message.apply_async(kwargs=payload, queue="notifications_default")
        return
    if row.source == "notification_task" and row.event_type == "send_call_notification":
        payload = _raw_payload(row)
        from src.app.tasks.notifications import send_call_notification

        send_call_notification.apply_async(kwargs=payload, queue="notifications_default")
        return
    if row.source == "retell_webhook" and raw:
        from src.app.retell.webhooks import handle_retell_webhook

        await handle_retell_webhook(body=raw.encode("utf-8"))
        return
    if row.source == "workflow_dispatch" and row.event_type == "dispatch_workflow_timer":
        payload = _raw_payload(row)
        from src.app.tasks.automation_workflow import dispatch_workflow_timer

        dispatch_workflow_timer.apply_async(
            kwargs={
                "timer_id": payload["timer_id"],
                "institution_id": str(row.institution_id),
                "location_id": str(row.location_id) if row.location_id else None,
                "run_id": payload["run_id"],
            },
            queue="workflow",
        )
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


def _response(
    row: DeadLetterEvent,
    *,
    include_resolution_note: bool = False,
) -> DeadLetterResponse:
    payload = row.redacted_payload or {}
    originating_run_id = _project_identifier(
        payload.get("run_id") or payload.get("workflow_run_id")
    )
    originating_timer_id = _project_identifier(payload.get("timer_id"))
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
        resolution_reason=row.resolution_reason,
        # Platform admins are intentionally excluded from PHI surfaces. The
        # bounded reason is safe globally; a tenant operator's free-text note
        # is returned only through the tenant-scoped route.
        resolution_note=row.resolution_note if include_resolution_note else None,
        replay_supported=_replay_supported(row),
        originating_run_id=originating_run_id,
        originating_timer_id=originating_timer_id,
    )


def _project_identifier(value: Any) -> str | None:
    if value is None or value == "[redacted]":
        return None
    return str(value)


def _replay_supported(row: DeadLetterEvent) -> bool:
    if row.status != DeadLetterStatus.OPEN.value or not row.raw_payload_encrypted:
        return False
    supported = {
        ("sms_task", "send_sms_message"),
        ("notification_task", "send_call_notification"),
        ("retell_webhook", "retell_webhook"),
        ("workflow_dispatch", "dispatch_workflow_timer"),
    }
    # Retell has used more than one event_type label over time; source plus a
    # retained raw webhook is sufficient because the handler re-verifies its
    # own durable idempotency record.
    return (row.source, row.event_type) in supported or row.source == "retell_webhook"
