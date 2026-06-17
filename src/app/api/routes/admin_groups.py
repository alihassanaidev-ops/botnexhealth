"""Admin routes for InstitutionGroups (DSO/practice-group oversight).

Super-admin only. A group owns N institutions and has one or more GROUP_ADMIN
users who get read-only, aggregate, cross-institution dashboards (see
``api/routes/group.py``). Assigning an institution to a group is a single FK
flip — no institution data moves and tenant isolation is unchanged.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.api.deps import get_current_admin
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background
from src.app.services.audit_decorator import audit
from src.app.services.institution_service import (
    InstitutionGroupService,
    InstitutionService,
)
from src.app.services.sms_privacy import safe_error_summary
from src.app.services.user_invite_service import UserInviteService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/institution-groups", tags=["Admin - Groups"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    # Email for the initial GROUP_ADMIN invite.
    email: str = Field(..., description="Email for the initial group-admin user invite")


class GroupResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    member_count: int = 0


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.GROUP_CREATE,
    resource=lambda request, data, _: f"group:{data.slug}",
    actor=AuditActor.ADMIN,
)
async def create_group(
    request: Request,
    data: GroupCreate,
    _: User = Depends(get_current_admin),
):
    """Create a group and invite its initial GROUP_ADMIN user."""
    async with get_db_session() as session:
        group_service = InstitutionGroupService(session)
        invite_service = UserInviteService(session)
        email = UserInviteService.normalize_email(data.email)

        if await group_service.get_by_slug(data.slug):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with slug '{data.slug}' already exists",
            )
        existing_user = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{email}' already exists",
            )

        try:
            group = await group_service.create(name=data.name, slug=data.slug)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with slug '{data.slug}' already exists (race condition)",
            )

        try:
            await invite_service.create_invited_user(
                email=email,
                role=UserRole.GROUP_ADMIN.value,
                institution_id=None,
                group_id=str(group.id),
            )
        except Exception as e:
            logger.error("Group-admin invite failed: %s", safe_error_summary(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )

        return GroupResponse(
            id=str(group.id), name=group.name, slug=group.slug,
            is_active=group.is_active, member_count=0,
        )


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    request: Request,
    _: User = Depends(get_current_admin),
):
    """List all groups with their active member-institution counts."""
    async with get_db_session() as session:
        rows = await InstitutionGroupService(session).list_with_counts()
        return [
            GroupResponse(
                id=str(g.id), name=g.name, slug=g.slug,
                is_active=g.is_active, member_count=count,
            )
            for g, count in rows
        ]


@router.post(
    "/{slug}/institutions/{inst_slug}",
    response_model=GroupResponse,
)
async def assign_institution(
    request: Request,
    slug: str,
    inst_slug: str,
    current_user: Annotated[User, Depends(get_current_admin)],
):
    """Assign an institution to a group."""
    async with get_db_session() as session:
        group_service = InstitutionGroupService(session)
        institution_service = InstitutionService(session)

        group = await group_service.get_by_slug(slug)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        institution = await institution_service.get_by_slug(inst_slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

        await group_service.set_member(institution, str(group.id))
        count = len(await _members(session, str(group.id)))
        await session.commit()

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.GROUP_ASSIGN,
        target_resource=f"group:{slug}/institution:{inst_slug}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role},
    )
    return GroupResponse(
        id=str(group.id), name=group.name, slug=group.slug,
        is_active=group.is_active, member_count=count,
    )


@router.delete(
    "/{slug}/institutions/{inst_slug}",
    response_model=GroupResponse,
)
async def unassign_institution(
    request: Request,
    slug: str,
    inst_slug: str,
    current_user: Annotated[User, Depends(get_current_admin)],
):
    """Remove an institution from a group."""
    async with get_db_session() as session:
        group_service = InstitutionGroupService(session)
        institution_service = InstitutionService(session)

        group = await group_service.get_by_slug(slug)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        institution = await institution_service.get_by_slug(inst_slug, include_inactive=True)
        if not institution or institution.group_id != group.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Institution is not a member of this group",
            )

        await group_service.set_member(institution, None)
        count = len(await _members(session, str(group.id)))
        await session.commit()

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.GROUP_UNASSIGN,
        target_resource=f"group:{slug}/institution:{inst_slug}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role},
    )
    return GroupResponse(
        id=str(group.id), name=group.name, slug=group.slug,
        is_active=group.is_active, member_count=count,
    )


async def _members(session, group_id: str) -> list[str]:
    from src.app.models.institution import Institution

    rows = (
        await session.execute(
            select(Institution.id).where(
                Institution.group_id == group_id,
                Institution.is_active.is_(True),
            )
        )
    ).all()
    return [str(r.id) for r in rows]
