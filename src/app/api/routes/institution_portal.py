"""
Institution portal routes.
"""

from __future__ import annotations

from datetime import time as dt_time
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, delete

from src.app.api.deps import (
    get_current_institution_admin,
    get_current_institution_or_location_user,
    get_current_location_admin,
)
from src.app.api.models import AuditLogPaginatedResponse, AuditLogResponse
from src.app.database import get_db_session
from src.app.models.user import User, UserRole
from src.app.models.audit_log import AuditLog
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.services.institution_service import InstitutionService
from src.app.services.supabase_service import SupabaseService
from src.app.services.audit import log_audit_background
from src.app.models.audit_log import AuditAction, AuditOutcome

router = APIRouter(prefix="/institution", tags=["Institution Portal"])


class InstitutionPortalMeResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: str
    institution_id: str | None
    location_id: str | None


class OperatingHoursEntry(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    is_open: bool = True
    open_time: str | None = Field(None, description="HH:MM")
    close_time: str | None = Field(None, description="HH:MM")


class OperatingHoursResponse(BaseModel):
    id: str
    location_id: str
    day_of_week: int
    is_open: bool
    open_time: str | None
    close_time: str | None

    @classmethod
    def from_model(cls, m: Any) -> "OperatingHoursResponse":
        return cls(
            id=str(m.id),
            location_id=str(m.location_id),
            day_of_week=m.day_of_week,
            is_open=m.is_open,
            open_time=m.open_time.strftime("%H:%M") if m.open_time else None,
            close_time=m.close_time.strftime("%H:%M") if m.close_time else None,
        )


class BulkOperatingHoursRequest(BaseModel):
    hours: list[OperatingHoursEntry] = Field(..., min_length=1, max_length=7)


class InstitutionLocationLiteResponse(BaseModel):
    id: str
    institution_id: str
    name: str
    slug: str
    is_active: bool
    phone: str | None
    timezone: str | None

    @classmethod
    def from_model(cls, loc: Any) -> "InstitutionLocationLiteResponse":
        return cls(
            id=str(loc.id),
            institution_id=str(loc.institution_id),
            name=loc.name,
            slug=loc.slug,
            is_active=loc.is_active,
            phone=loc.phone,
            timezone=loc.timezone,
        )


class InviteUserRequest(BaseModel):
    email: str = Field(..., description="Invitee email")


def _sanitize_target_resource(value: str) -> str:
    """
    Remove vendor names from institution-facing audit resources.
    """
    out = value
    out = re.sub(r"retell", "integration", out, flags=re.IGNORECASE)
    out = re.sub(r"nexhealth", "integration", out, flags=re.IGNORECASE)
    return out


def _sanitize_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Remove vendor-specific metadata keys for non-super-admin users.
    """
    if not metadata:
        return metadata
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        key_l = key.lower()
        if "retell" in key_l or "nexhealth" in key_l:
            continue
        sanitized[key] = value
    return sanitized or None


def _sanitize_audit_item(item: AuditLog) -> AuditLogResponse:
    actor = "SYSTEM" if item.actor == "RETELL_AGENT" else item.actor
    return AuditLogResponse(
        id=str(item.id),
        timestamp=item.timestamp,
        institution_id=str(item.institution_id) if item.institution_id else None,
        actor=actor,
        action=item.action,
        target_resource=_sanitize_target_resource(item.target_resource),
        outcome=item.outcome,
        audit_metadata=_sanitize_audit_metadata(item.audit_metadata),
    )


def _assert_location_scope(current_user: User, location_id: str) -> None:
    if current_user.role == UserRole.LOCATION_ADMIN.value and current_user.location_id != location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this location",
        )


@router.get("/me", response_model=InstitutionPortalMeResponse)
async def get_my_institution_config(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)]
):
    """
    Get non-sensitive profile context for institution portal users.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution"
        )
    async with get_db_session() as session:
        service = InstitutionService(session)
        institution = await service.get_by_id(current_user.institution_id)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Institution not found",
            )

    return InstitutionPortalMeResponse(
        id=str(institution.id),
        name=institution.name,
        slug=institution.slug,
        role=current_user.role,
        institution_id=current_user.institution_id,
        location_id=current_user.location_id,
    )


@router.get("/locations", response_model=list[InstitutionLocationLiteResponse])
async def list_portal_locations(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """
    Institution admins: all institution locations.
    Location admins/staff: only their assigned location.
    Sensitive integration fields are intentionally excluded.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment")

    async with get_db_session() as session:
        svc = InstitutionService(session)
        if current_user.role == UserRole.INSTITUTION_ADMIN.value:
            locations = await svc.list_locations(current_user.institution_id, include_inactive=True)
            return [InstitutionLocationLiteResponse.from_model(loc) for loc in locations]

        if not current_user.location_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No location assignment")
        from src.app.models.institution_location import InstitutionLocation
        loc_result = await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.id == current_user.location_id,
                InstitutionLocation.institution_id == current_user.institution_id,
            )
        )
        location = loc_result.scalar_one_or_none()
        if not location:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        return [InstitutionLocationLiteResponse.from_model(location)]


@router.get("/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse])
async def get_location_operating_hours(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment")
    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        _assert_location_scope(current_user, str(location.id))

        result = await session.execute(
            select(LocationOperatingHours)
            .where(LocationOperatingHours.location_id == location.id)
            .order_by(LocationOperatingHours.day_of_week)
        )
        return [OperatingHoursResponse.from_model(h) for h in result.scalars().all()]


@router.put("/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse])
async def set_location_operating_hours(
    loc_slug: str,
    data: BulkOperatingHoursRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """
    Institution admins can update timings for any location in their institution.
    Location admins can update timings for their own location.
    Staff cannot modify timings.
    """
    if current_user.role == UserRole.STAFF.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff cannot edit operating hours",
        )
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment")

    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        _assert_location_scope(current_user, str(location.id))

        days_seen = set()
        for entry in data.hours:
            if entry.day_of_week in days_seen:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Duplicate day_of_week: {entry.day_of_week}",
                )
            days_seen.add(entry.day_of_week)

        await session.execute(
            delete(LocationOperatingHours).where(
                LocationOperatingHours.location_id == location.id
            )
        )

        new_rows = []
        for entry in data.hours:
            row = LocationOperatingHours(
                location_id=location.id,
                day_of_week=entry.day_of_week,
                is_open=entry.is_open,
                open_time=dt_time.fromisoformat(entry.open_time) if entry.open_time else None,
                close_time=dt_time.fromisoformat(entry.close_time) if entry.close_time else None,
            )
            session.add(row)
            new_rows.append(row)
        await session.flush()
        return [OperatingHoursResponse.from_model(r) for r in new_rows]


@router.post("/users/invite-institution-admin", status_code=status.HTTP_201_CREATED)
async def invite_institution_admin(
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment")

    async with get_db_session() as session:
        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

        supabase = SupabaseService()
        response = supabase.invite_user(
            email=data.email,
            institution_id=current_user.institution_id,
            role=UserRole.INSTITUTION_ADMIN.value,
        )
        supabase_user_id = None
        if hasattr(response, "user") and hasattr(response.user, "id"):
            supabase_user_id = str(response.user.id)
        elif isinstance(response, dict) and "id" in response:
            supabase_user_id = str(response["id"])
        if not supabase_user_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invite did not return user id")

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.INSTITUTION_ADMIN.value,
            institution_id=current_user.institution_id,
            is_active=True,
        )
        session.add(user)

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"user:{data.email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.INSTITUTION_ADMIN.value,
            "institution_id": current_user.institution_id,
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Institution admin invite sent to {data.email}"}


@router.post("/locations/{loc_slug}/invite-location-admin", status_code=status.HTTP_201_CREATED)
async def invite_location_admin(
    loc_slug: str,
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment")
    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

        supabase = SupabaseService()
        response = supabase.invite_user(
            email=data.email,
            institution_id=current_user.institution_id,
            role=UserRole.LOCATION_ADMIN.value,
            location_id=str(location.id),
        )
        supabase_user_id = None
        if hasattr(response, "user") and hasattr(response.user, "id"):
            supabase_user_id = str(response.user.id)
        elif isinstance(response, dict) and "id" in response:
            supabase_user_id = str(response["id"])
        if not supabase_user_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invite did not return user id")

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.LOCATION_ADMIN.value,
            institution_id=current_user.institution_id,
            location_id=location.id,
            is_active=True,
        )
        session.add(user)

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"location:{loc_slug}/user:{data.email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.LOCATION_ADMIN.value,
            "institution_id": current_user.institution_id,
            "location_id": str(location.id),
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Location admin invite sent to {data.email}"}


@router.post("/locations/{loc_slug}/invite-staff", status_code=status.HTTP_201_CREATED)
async def invite_staff(
    loc_slug: str,
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_location_admin)],
):
    if not current_user.institution_id or not current_user.location_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment")
    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        if str(location.id) != str(current_user.location_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only invite staff for your location")

        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

        supabase = SupabaseService()
        response = supabase.invite_user(
            email=data.email,
            institution_id=current_user.institution_id,
            role=UserRole.STAFF.value,
            location_id=str(location.id),
        )
        supabase_user_id = None
        if hasattr(response, "user") and hasattr(response.user, "id"):
            supabase_user_id = str(response.user.id)
        elif isinstance(response, dict) and "id" in response:
            supabase_user_id = str(response["id"])
        if not supabase_user_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invite did not return user id")

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.STAFF.value,
            institution_id=current_user.institution_id,
            location_id=location.id,
            is_active=True,
        )
        session.add(user)

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"location:{loc_slug}/staff:{data.email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.STAFF.value,
            "institution_id": current_user.institution_id,
            "location_id": str(location.id),
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Staff invite sent to {data.email}"}


@router.get("/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_my_audit_logs(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100)
):
    """
    Institution admin: view all audit logs for the institution including sub-locations.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution"
        )

    async with get_db_session() as session:
        # Get total count
        count_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id)
        )
        total = count_result.scalar() or 0

        # Get paginated data
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id)
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = result.scalars().all()

        import math
        pages = math.ceil(total / size) if size > 0 else 0

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_AUDIT_LOGS,
            target_resource="institution:audit_logs",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "page": page,
                "size": size,
                "location_id": current_user.location_id,
            },
            institution_id=current_user.institution_id,
        )

        return AuditLogPaginatedResponse(
            items=[_sanitize_audit_item(item) for item in items],
            total=total,
            page=page,
            size=size,
            pages=pages
        )


@router.get("/location/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_location_audit_logs(
    current_user: Annotated[User, Depends(get_current_location_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
):
    """
    Location admin: view only audit logs for their own location.
    """
    if not current_user.institution_id or not current_user.location_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment")

    location_id = str(current_user.location_id)
    async with get_db_session() as session:
        filter_expr = AuditLog.audit_metadata["location_id"].astext == location_id
        count_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id, filter_expr)
        )
        total = count_result.scalar() or 0

        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id, filter_expr)
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = result.scalars().all()
        import math
        pages = math.ceil(total / size) if size > 0 else 0
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_AUDIT_LOGS,
            target_resource="location:audit_logs",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": location_id,
                "page": page,
                "size": size,
            },
            institution_id=current_user.institution_id,
        )
        return AuditLogPaginatedResponse(
            items=[_sanitize_audit_item(item) for item in items],
            total=total,
            page=page,
            size=size,
            pages=pages,
        )
