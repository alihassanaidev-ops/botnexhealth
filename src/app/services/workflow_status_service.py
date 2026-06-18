"""Service for Workflow Status CRUD (tenant-defined call workflow states)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.workflow_status import (
    DEFAULT_WORKFLOW_STATUSES,
    MAX_ACTIVE_WORKFLOW_STATUSES,
    WORKFLOW_STATUS_COLORS,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class WorkflowStatusService:
    """Manages per-institution workflow status definitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_statuses(
        self, institution_id: str, *, include_inactive: bool = False
    ) -> list[WorkflowStatus]:
        stmt = (
            select(WorkflowStatus)
            .where(WorkflowStatus.institution_id == institution_id)
            .order_by(WorkflowStatus.display_order, WorkflowStatus.created_at)
        )
        if not include_inactive:
            stmt = stmt.where(WorkflowStatus.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_status(self, institution_id: str, status_id: str) -> WorkflowStatus | None:
        result = await self.session.execute(
            select(WorkflowStatus).where(
                WorkflowStatus.id == status_id,
                WorkflowStatus.institution_id == institution_id,
            )
        )
        return result.scalar_one_or_none()

    async def _active_count(self, institution_id: str) -> int:
        result = await self.session.execute(
            select(func.count(WorkflowStatus.id)).where(
                WorkflowStatus.institution_id == institution_id,
                WorkflowStatus.is_active.is_(True),
            )
        )
        return int(result.scalar_one())

    async def _name_taken(
        self, institution_id: str, name: str, *, exclude_id: str | None = None
    ) -> bool:
        """Case-insensitive name uniqueness within the institution."""
        stmt = select(WorkflowStatus.id).where(
            WorkflowStatus.institution_id == institution_id,
            func.lower(WorkflowStatus.name) == name.strip().lower(),
        )
        if exclude_id:
            stmt = stmt.where(WorkflowStatus.id != exclude_id)
        result = await self.session.execute(stmt)
        return result.first() is not None

    @staticmethod
    def _validate_color(color: str) -> str:
        if color not in WORKFLOW_STATUS_COLORS:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"color must be one of {', '.join(WORKFLOW_STATUS_COLORS)}",
            )
        return color

    async def create_status(
        self,
        institution_id: str,
        *,
        name: str,
        color: str = "zinc",
        display_order: int | None = None,
    ) -> WorkflowStatus:
        name = name.strip()
        if not name:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST, detail="name is required"
            )
        self._validate_color(color)

        if await self._active_count(institution_id) >= MAX_ACTIVE_WORKFLOW_STATUSES:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Limit reached: at most {MAX_ACTIVE_WORKFLOW_STATUSES} active statuses. "
                    "Archive one to add another."
                ),
            )
        if await self._name_taken(institution_id, name):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f'A status named "{name}" already exists.',
            )

        if display_order is None:
            existing = await self.list_statuses(institution_id, include_inactive=True)
            display_order = max((s.display_order for s in existing), default=-1) + 1

        ws = WorkflowStatus(
            id=str(uuid4()),
            institution_id=institution_id,
            name=name,
            color=color,
            display_order=display_order,
        )
        self.session.add(ws)
        await self.session.flush()
        return ws

    async def update_status(
        self, ws: WorkflowStatus, **updates: Any
    ) -> WorkflowStatus:
        if "name" in updates and updates["name"] is not None:
            new_name = updates["name"].strip()
            if not new_name:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST, detail="name cannot be empty"
                )
            if await self._name_taken(ws.institution_id, new_name, exclude_id=ws.id):
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=f'A status named "{new_name}" already exists.',
                )
            updates["name"] = new_name
        if "color" in updates and updates["color"] is not None:
            self._validate_color(updates["color"])
        # Reactivating must respect the cap.
        if updates.get("is_active") is True and not ws.is_active:
            if await self._active_count(ws.institution_id) >= MAX_ACTIVE_WORKFLOW_STATUSES:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"Limit reached: at most {MAX_ACTIVE_WORKFLOW_STATUSES} active statuses.",
                )

        allowed = {"name", "color", "display_order", "is_active"}
        for key, value in updates.items():
            if key in allowed and value is not None:
                setattr(ws, key, value)
        await self.session.flush()
        return ws

    async def delete_status(self, ws: WorkflowStatus, *, hard_delete: bool = False) -> None:
        if hard_delete:
            await self.session.delete(ws)
        else:
            ws.is_active = False
        await self.session.flush()

    async def seed_defaults(self, institution_id: str) -> int:
        """Seed the default status set for an institution that has none.

        Idempotent: skips if any status already exists. Returns rows created.
        """
        existing = await self.list_statuses(institution_id, include_inactive=True)
        if existing:
            return 0
        for name, color, order in DEFAULT_WORKFLOW_STATUSES:
            self.session.add(
                WorkflowStatus(
                    id=str(uuid4()),
                    institution_id=institution_id,
                    name=name,
                    color=color,
                    display_order=order,
                )
            )
        await self.session.flush()
        return len(DEFAULT_WORKFLOW_STATUSES)
