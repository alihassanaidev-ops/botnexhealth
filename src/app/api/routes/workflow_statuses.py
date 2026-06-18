"""Workflow Status definition CRUD for institutions.

Tenant-defined, human-assigned workflow states for calls (Pending, Completed,
…). Managed by INSTITUTION_ADMIN or LOCATION_ADMIN; assignable to a call by any
active institution user (see PATCH /institution/calls/{id}/status).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.app.api.deps import (
    get_current_active_user,
    get_current_institution_or_location_admin,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.models.workflow_status import WorkflowStatus
from src.app.services.workflow_status_service import WorkflowStatusService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/statuses", tags=["Workflow Statuses"])


# ── Request / Response models ──────────────────────────────────────────────


class CreateStatusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = "zinc"
    display_order: int | None = None


class UpdateStatusRequest(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    color: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class WorkflowStatusResponse(BaseModel):
    id: str
    institution_id: str
    name: str
    color: str
    display_order: int
    is_active: bool
    created_at: str


def _to_response(s: WorkflowStatus) -> WorkflowStatusResponse:
    return WorkflowStatusResponse(
        id=s.id,
        institution_id=s.institution_id,
        name=s.name,
        color=s.color,
        display_order=s.display_order,
        is_active=s.is_active,
        created_at=s.created_at.isoformat(),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[WorkflowStatusResponse])
@limiter.limit(RATE_READ)
async def list_statuses(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    include_inactive: bool = Query(False),
) -> list[WorkflowStatusResponse]:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    async with get_db_session() as session:
        svc = WorkflowStatusService(session)
        statuses = await svc.list_statuses(
            current_user.institution_id, include_inactive=include_inactive
        )
        return [_to_response(s) for s in statuses]


@router.post("", response_model=WorkflowStatusResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
async def create_status(
    request: Request,
    body: CreateStatusRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> WorkflowStatusResponse:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    async with get_db_session() as session:
        svc = WorkflowStatusService(session)
        ws = await svc.create_status(
            current_user.institution_id,
            name=body.name,
            color=body.color,
            display_order=body.display_order,
        )
        await session.commit()
        await session.refresh(ws)
        return _to_response(ws)


@router.patch("/{status_id}", response_model=WorkflowStatusResponse)
@limiter.limit(RATE_WRITE)
async def update_status(
    request: Request,
    status_id: str,
    body: UpdateStatusRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> WorkflowStatusResponse:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    async with get_db_session() as session:
        svc = WorkflowStatusService(session)
        ws = await svc.get_status(current_user.institution_id, status_id)
        if not ws:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status not found")
        await svc.update_status(ws, **body.model_dump(exclude_unset=True))
        await session.commit()
        await session.refresh(ws)
        return _to_response(ws)


@router.delete("/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
async def delete_status(
    request: Request,
    status_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    hard: bool = Query(False),
) -> None:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    async with get_db_session() as session:
        svc = WorkflowStatusService(session)
        ws = await svc.get_status(current_user.institution_id, status_id)
        if not ws:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status not found")
        await svc.delete_status(ws, hard_delete=hard)
        await session.commit()
