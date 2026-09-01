"""Tenant-scoped operator API for undeliverable work.

The shared implementation lives beside the original platform-admin API. This
router is separate so the route-level RBAC and audit coverage tests see every
tenant endpoint as an explicit surface rather than an alias hidden on a second
router variable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.app.api.deps import get_current_institution_or_location_admin
from src.app.api.permissions import Permission, require_permission
from src.app.api.routes.dead_letter import (
    DeadLetterListResponse,
    DeadLetterResponse,
    DiscardDeadLetterRequest,
    _discard_dead_letter_event,
    _list_dead_letter_events,
    _replay_dead_letter_event,
)
from src.app.models.dead_letter_event import DeadLetterStatus
from src.app.models.user import User

router = APIRouter(
    prefix="/institution/undeliverables",
    tags=["Institution - Undeliverables"],
)


@router.get("", response_model=DeadLetterListResponse)
async def list_institution_undeliverables(
    _: Annotated[User, Depends(get_current_institution_or_location_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status_filter: str = Query(DeadLetterStatus.OPEN.value, alias="status"),
    source: str | None = None,
) -> DeadLetterListResponse:
    """List only the events visible through the caller's tenant RLS scope."""
    return await _list_dead_letter_events(
        page=page,
        size=size,
        status_filter=status_filter,
        source=source,
        include_resolution_note=True,
    )


@router.post("/{event_id}/discard", response_model=DeadLetterResponse)
async def discard_institution_undeliverable(
    event_id: str,
    request: DiscardDeadLetterRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> DeadLetterResponse:
    return await _discard_dead_letter_event(
        event_id=event_id,
        current_user=current_user,
        request=request,
        include_resolution_note=True,
    )


@router.post(
    "/{event_id}/replay",
    response_model=DeadLetterResponse,
    dependencies=[Depends(require_permission(Permission.WRITE_REPLAY))],
)
async def replay_institution_undeliverable(
    event_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> DeadLetterResponse:
    return await _replay_dead_letter_event(
        event_id=event_id,
        current_user=current_user,
        include_resolution_note=True,
    )
