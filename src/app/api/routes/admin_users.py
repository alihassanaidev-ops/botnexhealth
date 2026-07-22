"""Super-admin user management.

A first-class replacement for the one-off ``delete_user_and_location`` script:
list/search every user across the platform and remove (soft-delete) or reinvite
any of them. "Remove" sets ``deleted_at`` / ``is_active=False`` via
``User.mark_deleted()`` — the partial unique index on ``users(email) WHERE
deleted_at IS NULL`` frees the email for re-invite, and active-user listings
(``list_locations``, invite uniqueness checks) already filter
``deleted_at IS NULL`` so the slot reads free immediately.

SUPER_ADMIN accounts are intentionally not removable from this surface; their
lifecycle is a deliberate, out-of-band concern.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import get_current_admin
from src.app.api.pagination import PaginationQuery, page_count, paginate
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.audit import log_audit
from src.app.services.sms_privacy import safe_error_summary
from src.app.services.user_invite_service import UserInviteService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users", tags=["Admin - Users"])


class AdminUserRow(BaseModel):
    """A user row enriched with institution/location display fields."""

    id: str
    email: str
    role: str
    is_active: bool
    invite_status: str
    deleted_at: str | None

    institution_id: str | None
    institution_name: str | None
    institution_slug: str | None

    location_id: str | None
    location_name: str | None
    location_slug: str | None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserRow]
    total: int
    page: int
    size: int
    pages: int


class UserActionResponse(BaseModel):
    message: str
    user_id: str


# Institution-scoped roles a SUPER_ADMIN may seat via this surface. SUPER_ADMIN
# is intentionally excluded — those are provisioned through the separate
# super-admin invite flow, never against an institution.
_INVITABLE_ROLES: frozenset[str] = frozenset(
    {
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
        UserRole.STAFF.value,
    }
)


class InviteInstitutionUserRequest(BaseModel):
    email: str
    institution_id: str
    # Defaults to institution admin — the common case (seating a second admin
    # when the clinic's own admin is unavailable). LOCATION_ADMIN/STAFF allowed.
    role: str = UserRole.INSTITUTION_ADMIN.value
    location_id: str | None = None


class InviteInstitutionUserResponse(BaseModel):
    message: str
    user_id: str
    email: str
    role: str
    institution_id: str
    invite_status: str


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    _: Annotated[User, Depends(get_current_admin)],
    q: str | None = Query(None, description="Email substring (case-insensitive)"),
    role: str | None = Query(None, description="Filter by UserRole"),
    institution_id: str | None = Query(None),
    location_id: str | None = Query(None),
    status_filter: Literal["active", "pending", "deleted", "all"] = Query(
        "active", alias="status"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
):
    """List/search users across the platform (SUPER_ADMIN only).

    ``status`` semantics (active/pending are mutually exclusive so the filter
    labels match what's shown):
      * ``active``  — not soft-deleted and invite accepted (default)
      * ``pending`` — not soft-deleted and invite_status == PENDING
      * ``deleted`` — soft-deleted rows only
      * ``all``     — everything
    """
    async with get_db_session() as session:
        from src.app.models.user import InviteStatus

        stmt = select(User)

        if status_filter == "deleted":
            stmt = stmt.where(User.deleted_at.is_not(None))
        elif status_filter == "all":
            pass
        else:
            stmt = stmt.where(User.deleted_at.is_(None))
            if status_filter == "pending":
                stmt = stmt.where(User.invite_status == InviteStatus.PENDING.value)
            else:  # active
                stmt = stmt.where(User.invite_status == InviteStatus.ACCEPTED.value)

        if q:
            stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
        if role:
            stmt = stmt.where(User.role == role)
        if institution_id:
            stmt = stmt.where(User.institution_id == institution_id)
        if location_id:
            stmt = stmt.where(User.location_id == location_id)

        users, total = await paginate(
            PaginationQuery(session, stmt.order_by(User.email)),
            page=page,
            size=size,
        )

        # Batch-fetch institution/location display fields (avoids N+1).
        inst_ids = {u.institution_id for u in users if u.institution_id}
        loc_ids = {u.location_id for u in users if u.location_id}

        inst_by_id: dict[str, Institution] = {}
        if inst_ids:
            rows = await session.execute(
                select(Institution).where(Institution.id.in_(inst_ids))
            )
            inst_by_id = {str(i.id): i for i in rows.scalars().all()}

        loc_by_id: dict[str, InstitutionLocation] = {}
        if loc_ids:
            rows = await session.execute(
                select(InstitutionLocation).where(InstitutionLocation.id.in_(loc_ids))
            )
            loc_by_id = {str(loc.id): loc for loc in rows.scalars().all()}

        items = []
        for u in users:
            inst = inst_by_id.get(str(u.institution_id)) if u.institution_id else None
            loc = loc_by_id.get(str(u.location_id)) if u.location_id else None
            items.append(
                AdminUserRow(
                    id=str(u.id),
                    email=u.email,
                    role=u.role,
                    is_active=u.is_active,
                    invite_status=u.invite_status,
                    deleted_at=u.deleted_at.isoformat() if u.deleted_at else None,
                    institution_id=str(u.institution_id) if u.institution_id else None,
                    institution_name=inst.name if inst else None,
                    institution_slug=inst.slug if inst else None,
                    location_id=str(u.location_id) if u.location_id else None,
                    location_name=loc.name if loc else None,
                    location_slug=loc.slug if loc else None,
                )
            )

        return AdminUserListResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=page_count(total, size),
        )


async def _load_actionable_user(session, user_id: str, current_admin: User) -> User:
    """Fetch an active user that the admin is allowed to act on.

    Shared guards for remove/reinvite: 404 if missing or already soft-deleted,
    400 if it's the admin's own account, 403 if the target is a SUPER_ADMIN.
    """
    target = (
        await session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if str(target.id) == str(current_admin.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot perform this action on your own account",
        )
    if target.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin accounts cannot be managed from the dashboard",
        )
    return target


@router.delete("/{user_id}", response_model=UserActionResponse)
async def remove_user(
    user_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Remove (soft-delete) a user, freeing their email for re-invite.

    We intentionally do NOT null ``location_id`` here. It's unnecessary for the
    soft-delete to free the email / location slot (active-user queries filter
    ``deleted_at IS NULL``). Note for the future: if location *hard*-delete is
    ever wired into the UI, the ``users.location_id`` NO-ACTION FK will block it
    while soft-deleted rows still reference the location — that path must null
    the referencing rows first (this is what the old script did).
    """
    async with get_db_session() as session:
        target = await _load_actionable_user(session, user_id, current_admin)
        target_role = target.role
        target_institution_id = target.institution_id
        target_location_id = target.location_id
        target.mark_deleted()

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.USER_DELETE,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_admin.role,
            "admin_id": str(current_admin.id),
            "target_user_id": user_id,
            "target_role": target_role,
            "institution_id": (
                str(target_institution_id) if target_institution_id else None
            ),
            "location_id": str(target_location_id) if target_location_id else None,
        },
        institution_id=str(target_institution_id) if target_institution_id else None,
        user_id=str(current_admin.id),
        location_id=str(target_location_id) if target_location_id else None,
    )
    return UserActionResponse(message="User removed", user_id=user_id)


@router.post("/{user_id}/reinvite", response_model=UserActionResponse)
async def reinvite_user(
    user_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Rotate a user's invite token and resend the invite email."""
    async with get_db_session() as session:
        target = await _load_actionable_user(session, user_id, current_admin)
        target_institution_id = target.institution_id
        target_location_id = target.location_id
        invite_service = UserInviteService(session)
        try:
            await invite_service.reinvite_user(target)
        except Exception as e:
            logger.error("Admin reinvite failed: %s", safe_error_summary(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to re-invite user",
            )

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.USER_REINVITED,
        target_resource=f"user:{user_id}:reinvite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_admin.role,
            "admin_id": str(current_admin.id),
            "target_user_id": user_id,
            "institution_id": (
                str(target_institution_id) if target_institution_id else None
            ),
            "location_id": str(target_location_id) if target_location_id else None,
        },
        institution_id=str(target_institution_id) if target_institution_id else None,
        user_id=str(current_admin.id),
        location_id=str(target_location_id) if target_location_id else None,
    )
    return UserActionResponse(message="Invite re-sent", user_id=user_id)


@router.post("/invite", response_model=InviteInstitutionUserResponse)
async def invite_institution_user(
    body: InviteInstitutionUserRequest,
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Invite an institution-scoped user (default: institution admin).

    SUPER_ADMIN-only. Lets a platform admin seat an admin for any institution
    from the dashboard — e.g. a second institution admin when the clinic's own
    admin can't send the invite themselves. Never provisions a SUPER_ADMIN.
    """
    email = UserInviteService.normalize_email(body.email)
    role = body.role.strip().upper()

    if role not in _INVITABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "role must be one of: "
                + ", ".join(sorted(_INVITABLE_ROLES))
            ),
        )
    # A location-scoped role needs a location; an institution admin must not be
    # pinned to a single location.
    if role in {UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value} and not body.location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"location_id is required for role {role}",
        )
    if role == UserRole.INSTITUTION_ADMIN.value and body.location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="location_id must not be set for an institution admin",
        )

    async with get_db_session() as session:
        institution = (
            await session.execute(
                select(Institution).where(Institution.id == body.institution_id)
            )
        ).scalar_one_or_none()
        if institution is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Institution not found",
            )

        # The partial unique index excludes soft-deleted rows, so only an active
        # user with this email is a real conflict.
        existing = (
            await session.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists",
            )

        if body.location_id is not None:
            loc = (
                await session.execute(
                    select(InstitutionLocation).where(
                        InstitutionLocation.id == body.location_id,
                        InstitutionLocation.institution_id == body.institution_id,
                    )
                )
            ).scalar_one_or_none()
            if loc is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="location_id does not belong to this institution",
                )

        invite_service = UserInviteService(session)
        try:
            user = await invite_service.create_invited_user(
                email=email,
                role=role,
                institution_id=body.institution_id,
                location_id=body.location_id,
            )
        except Exception as e:
            logger.error("Admin institution invite failed: %s", safe_error_summary(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create invite",
            )
        user_id = str(user.id)

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.USER_INVITED,
        target_resource=f"user:{user_id}:invite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_admin.role,
            "admin_id": str(current_admin.id),
            "target_user_id": user_id,
            "target_role": role,
            "institution_id": body.institution_id,
            "location_id": body.location_id,
        },
        institution_id=body.institution_id,
        user_id=str(current_admin.id),
        location_id=body.location_id,
    )
    return InviteInstitutionUserResponse(
        message="Invite sent",
        user_id=user_id,
        email=email,
        role=role,
        institution_id=body.institution_id,
        invite_status=InviteStatus.PENDING.value,
    )
