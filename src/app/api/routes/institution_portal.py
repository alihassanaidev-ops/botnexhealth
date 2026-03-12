"""
Institution portal routes.
"""

from __future__ import annotations

from datetime import time as dt_time
import re
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, delete

from src.app.api.deps import (
    get_current_institution_admin,
    get_current_institution_or_location_admin,
    get_current_institution_or_location_user,
    get_current_location_admin,
)
from src.app.api.models import AuditLogPaginatedResponse, AuditLogResponse
from src.app.database import get_db_session
from src.app.models.user import User, UserRole, InviteStatus
from src.app.models.audit_log import AuditLog
from src.app.models.insurance_plan import InsurancePlan
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.services.institution_service import InstitutionService
from src.app.services.invite_cooldown import apply_invite_cooldown, ensure_invite_cooldown
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


class LocationTimezoneUpdateRequest(BaseModel):
    timezone: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="IANA timezone, e.g. America/New_York",
    )


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


class TransferNumberRequest(BaseModel):
    phone_number: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^\+[1-9]\d{7,14}$",
        description="E.164 format (e.g. +923001234567)",
    )
    department: str = Field(..., min_length=1, max_length=255)


class TransferNumberResponse(BaseModel):
    id: str
    location_id: str
    location_slug: str
    location_name: str
    phone_number: str
    department: str


class InviteUserRequest(BaseModel):
    email: str = Field(..., description="Invitee email")


class InstitutionUserInviteRequest(BaseModel):
    email: str = Field(..., description="Invitee email")
    role: str = Field(..., description="INSTITUTION_ADMIN | LOCATION_ADMIN | STAFF")
    location_slug: str | None = Field(
        None, description="Required for LOCATION_ADMIN and STAFF"
    )


class InstitutionUserRowResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    invite_status: str
    institution_id: str | None
    location_id: str | None
    location_name: str | None


class UserActionResponse(BaseModel):
    message: str
    user_id: str


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


def _validate_invite_role(role: str) -> str:
    normalized = role.strip().upper()
    allowed = {
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
    }
    if normalized not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{role}'. Allowed: {', '.join(sorted(allowed))}",
        )
    return normalized


def _assert_location_scope(current_user: User, location_id: str) -> None:
    if (
        current_user.role == UserRole.LOCATION_ADMIN.value
        and current_user.location_id != location_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this location",
        )


@router.get("/me", response_model=InstitutionPortalMeResponse)
async def get_my_institution_config(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """
    Get non-sensitive profile context for institution portal users.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        if current_user.role == UserRole.INSTITUTION_ADMIN.value:
            locations = await svc.list_locations(
                current_user.institution_id, include_inactive=True
            )
            return [
                InstitutionLocationLiteResponse.from_model(loc) for loc in locations
            ]

        if not current_user.location_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No location assignment"
            )
        from src.app.models.institution_location import InstitutionLocation

        loc_result = await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.id == current_user.location_id,
                InstitutionLocation.institution_id == current_user.institution_id,
            )
        )
        location = loc_result.scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        return [InstitutionLocationLiteResponse.from_model(location)]


@router.get(
    "/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse]
)
async def get_location_operating_hours(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )
    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        _assert_location_scope(current_user, str(location.id))

        result = await session.execute(
            select(LocationOperatingHours)
            .where(LocationOperatingHours.location_id == location.id)
            .order_by(LocationOperatingHours.day_of_week)
        )
        return [OperatingHoursResponse.from_model(h) for h in result.scalars().all()]


@router.put(
    "/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse]
)
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
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
                open_time=dt_time.fromisoformat(entry.open_time)
                if entry.open_time
                else None,
                close_time=dt_time.fromisoformat(entry.close_time)
                if entry.close_time
                else None,
            )
            session.add(row)
            new_rows.append(row)
        await session.flush()
        return [OperatingHoursResponse.from_model(r) for r in new_rows]


@router.patch(
    "/locations/{loc_slug}/timezone", response_model=InstitutionLocationLiteResponse
)
async def update_location_timezone(
    loc_slug: str,
    data: LocationTimezoneUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """
    Institution admins can update timezone for any location in their institution.
    Location admins can update timezone for their own location.
    Staff cannot modify timezone.
    """
    if current_user.role == UserRole.STAFF.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff cannot update timezone",
        )
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    timezone_value = data.timezone.strip()
    try:
        ZoneInfo(timezone_value)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid timezone '{timezone_value}'",
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        _assert_location_scope(current_user, str(location.id))

        location.timezone = timezone_value
        await session.flush()

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"institution:location:{loc_slug}:timezone",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "location_slug": loc_slug,
            "timezone": timezone_value,
        },
        institution_id=current_user.institution_id,
    )

    return InstitutionLocationLiteResponse.from_model(location)


# =============================================================================
# Transfer Numbers
# =============================================================================


@router.get("/transfer-numbers", response_model=list[TransferNumberResponse])
async def list_transfer_numbers(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """List transfer numbers for the institution (location admins see only their location)."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation
        from src.app.models.institution_location_transfer_number import (
            InstitutionLocationTransferNumber,
        )

        stmt = (
            select(InstitutionLocationTransferNumber, InstitutionLocation)
            .join(
                InstitutionLocation,
                InstitutionLocation.id == InstitutionLocationTransferNumber.location_id,
            )
            .where(
                InstitutionLocationTransferNumber.institution_id
                == current_user.institution_id,
            )
        )

        if current_user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
            if not current_user.location_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Location-scoped account is missing location assignment",
                )
            stmt = stmt.where(
                InstitutionLocationTransferNumber.location_id
                == str(current_user.location_id)
            )

        rows = (
            await session.execute(
                stmt.order_by(InstitutionLocation.name, InstitutionLocationTransferNumber.department)
            )
        ).all()

        return [
            TransferNumberResponse(
                id=str(tn.id),
                location_id=str(loc.id),
                location_slug=loc.slug,
                location_name=loc.name,
                phone_number=tn.phone_number,
                department=tn.department,
            )
            for tn, loc in rows
        ]


@router.post(
    "/locations/{loc_slug}/transfer-numbers",
    response_model=TransferNumberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transfer_number(
    loc_slug: str,
    data: TransferNumberRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Create a transfer number for a location."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation
        from src.app.models.institution_location_transfer_number import (
            InstitutionLocationTransferNumber,
        )

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        entry = InstitutionLocationTransferNumber(
            institution_id=current_user.institution_id,
            location_id=str(location.id),
            phone_number=data.phone_number,
            department=data.department,
        )
        session.add(entry)
        await session.flush()

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/transfer_number:{entry.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "create_transfer_number",
                "department": data.department,
            },
            institution_id=current_user.institution_id,
        )

        return TransferNumberResponse(
            id=str(entry.id),
            location_id=str(location.id),
            location_slug=location.slug,
            location_name=location.name,
            phone_number=entry.phone_number,
            department=entry.department,
        )


@router.patch(
    "/locations/{loc_slug}/transfer-numbers/{transfer_id}",
    response_model=TransferNumberResponse,
)
async def update_transfer_number(
    loc_slug: str,
    transfer_id: str,
    data: TransferNumberRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Update a transfer number for a location."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation
        from src.app.models.institution_location_transfer_number import (
            InstitutionLocationTransferNumber,
        )

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        entry = (
            await session.execute(
                select(InstitutionLocationTransferNumber).where(
                    InstitutionLocationTransferNumber.id == transfer_id,
                    InstitutionLocationTransferNumber.location_id == str(location.id),
                    InstitutionLocationTransferNumber.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transfer number not found",
            )

        entry.phone_number = data.phone_number
        entry.department = data.department

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/transfer_number:{transfer_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "update_transfer_number",
                "department": data.department,
            },
            institution_id=current_user.institution_id,
        )

        return TransferNumberResponse(
            id=str(entry.id),
            location_id=str(location.id),
            location_slug=location.slug,
            location_name=location.name,
            phone_number=entry.phone_number,
            department=entry.department,
        )


@router.delete(
    "/locations/{loc_slug}/transfer-numbers/{transfer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_transfer_number(
    loc_slug: str,
    transfer_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Delete a transfer number for a location."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation
        from src.app.models.institution_location_transfer_number import (
            InstitutionLocationTransferNumber,
        )

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        entry = (
            await session.execute(
                select(InstitutionLocationTransferNumber).where(
                    InstitutionLocationTransferNumber.id == transfer_id,
                    InstitutionLocationTransferNumber.location_id == str(location.id),
                    InstitutionLocationTransferNumber.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transfer number not found",
            )

        await session.delete(entry)

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/transfer_number:{transfer_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "delete_transfer_number",
            },
            institution_id=current_user.institution_id,
        )

    return None

@router.post("/users/invite-institution-admin", status_code=status.HTTP_201_CREATED)
async def invite_institution_admin(
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invite did not return user id",
            )

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.INSTITUTION_ADMIN.value,
            institution_id=current_user.institution_id,
            invite_status=InviteStatus.PENDING.value,
            is_active=True,
        )
        session.add(user)
        apply_invite_cooldown(actor)

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


@router.get("/users", response_model=list[InstitutionUserRowResponse])
async def list_institution_users(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    """
    List all institution-scoped users (institution admins, location admins, staff)
    for institution admin user management.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        users = (
            (
                await session.execute(
                    select(User).where(
                        User.institution_id == current_user.institution_id,
                        User.role.in_(
                            [
                                UserRole.INSTITUTION_ADMIN.value,
                                UserRole.LOCATION_ADMIN.value,
                                UserRole.STAFF.value,
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

        location_ids = [u.location_id for u in users if u.location_id]
        location_name_by_id: dict[str, str] = {}
        if location_ids:
            location_rows = (
                (
                    await session.execute(
                        select(InstitutionLocation).where(
                            InstitutionLocation.id.in_(location_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            location_name_by_id = {str(loc.id): loc.name for loc in location_rows}

        return [
            InstitutionUserRowResponse(
                id=str(user.id),
                email=user.email,
                role=user.role,
                is_active=user.is_active,
                invite_status=user.invite_status,
                institution_id=str(user.institution_id)
                if user.institution_id
                else None,
                location_id=str(user.location_id) if user.location_id else None,
                location_name=location_name_by_id.get(str(user.location_id))
                if user.location_id
                else None,
            )
            for user in users
        ]


@router.post(
    "/users/invite",
    response_model=UserActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_institution_user(
    data: InstitutionUserInviteRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    """
    Invite institution users with role + optional location assignment.

    - INSTITUTION_ADMIN: no location assignment
    - LOCATION_ADMIN: location_slug required
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    role = _validate_invite_role(data.role)

    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        from src.app.models.institution_location import InstitutionLocation

        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

        location_id: str | None = None
        if role == UserRole.LOCATION_ADMIN.value:
            if not data.location_slug:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="location_slug is required for LOCATION_ADMIN",
                )
            location = (
                await session.execute(
                    select(InstitutionLocation).where(
                        InstitutionLocation.slug == data.location_slug,
                        InstitutionLocation.institution_id
                        == current_user.institution_id,
                    )
                )
            ).scalar_one_or_none()
            if not location:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
                )
            location_id = str(location.id)

        supabase = SupabaseService()
        response = supabase.invite_user(
            email=data.email,
            institution_id=current_user.institution_id,
            role=role,
            location_id=location_id,
        )
        supabase_user_id = None
        if hasattr(response, "user") and hasattr(response.user, "id"):
            supabase_user_id = str(response.user.id)
        elif isinstance(response, dict) and "id" in response:
            supabase_user_id = str(response["id"])
        if not supabase_user_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invite did not return user id",
            )

        created = User(
            id=supabase_user_id,
            email=data.email,
            role=role,
            institution_id=current_user.institution_id,
            location_id=location_id,
            invite_status=InviteStatus.PENDING.value,
            is_active=True,
        )
        session.add(created)
        apply_invite_cooldown(actor)

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"user:{data.email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": role,
            "institution_id": current_user.institution_id,
            "location_slug": data.location_slug,
            "location_id": location_id,
        },
        institution_id=current_user.institution_id,
    )
    return UserActionResponse(
        message=f"Invite sent to {data.email}", user_id=supabase_user_id
    )


@router.post("/users/{user_id}/deactivate", response_model=UserActionResponse)
async def deactivate_institution_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    """
    Deactivate institution-scoped user immediately.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )
    if str(current_user.id) == str(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    async with get_db_session() as session:
        target = (
            await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.institution_id == current_user.institution_id,
                    User.role.in_(
                        [
                            UserRole.INSTITUTION_ADMIN.value,
                            UserRole.LOCATION_ADMIN.value,
                            UserRole.STAFF.value,
                        ]
                    ),
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        target.is_active = False

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_DELETE,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "institution_id": current_user.institution_id,
            "deactivated_user_id": user_id,
        },
        institution_id=current_user.institution_id,
    )
    return UserActionResponse(message="User deactivated", user_id=user_id)


@router.post("/users/{user_id}/reinvite", response_model=UserActionResponse)
async def reinvite_institution_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    """
    Reinvite an institution-scoped user: deletes old Supabase user and
    creates a fresh invite/local user row with a new UUID.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        target = (
            await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.institution_id == current_user.institution_id,
                    User.role.in_(
                        [
                            UserRole.INSTITUTION_ADMIN.value,
                            UserRole.LOCATION_ADMIN.value,
                            UserRole.STAFF.value,
                        ]
                    ),
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )
        if str(target.id) == str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reinvite your own account",
            )

        old_user_id = str(target.id)
        old_email = target.email
        old_role = target.role
        old_location_id = str(target.location_id) if target.location_id else None
        old_is_active = target.is_active

        supabase = SupabaseService()
        try:
            supabase.delete_user(old_user_id)
        except Exception:
            # Continue: if already deleted on Supabase, reinvite should still proceed.
            pass

        response = supabase.invite_user(
            email=old_email,
            institution_id=current_user.institution_id,
            role=old_role,
            location_id=old_location_id,
        )
        new_supabase_user_id = None
        if hasattr(response, "user") and hasattr(response.user, "id"):
            new_supabase_user_id = str(response.user.id)
        elif isinstance(response, dict) and "id" in response:
            new_supabase_user_id = str(response["id"])
        if not new_supabase_user_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invite did not return user id",
            )

        await session.delete(target)
        await session.flush()

        replacement = User(
            id=new_supabase_user_id,
            email=old_email,
            role=old_role,
            institution_id=current_user.institution_id,
            location_id=old_location_id,
            invite_status=InviteStatus.PENDING.value,
            is_active=old_is_active,
        )
        session.add(replacement)
        apply_invite_cooldown(actor)

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"user:{old_email}:reinvite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "institution_id": current_user.institution_id,
            "old_user_id": old_user_id,
            "new_user_id": new_supabase_user_id,
            "role": old_role,
            "location_id": old_location_id,
        },
        institution_id=current_user.institution_id,
    )
    return UserActionResponse(
        message=f"Reinvite sent to {old_email}", user_id=new_supabase_user_id
    )


@router.get("/location/users", response_model=list[InstitutionUserRowResponse])
async def list_location_users(
    current_user: Annotated[User, Depends(get_current_location_admin)],
):
    """
    Location admins: list STAFF users assigned to their location.
    """
    if not current_user.institution_id or not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        users = (
            (
                await session.execute(
                    select(User).where(
                        User.institution_id == current_user.institution_id,
                        User.location_id == current_user.location_id,
                        User.role == UserRole.STAFF.value,
                    )
                )
            )
            .scalars()
            .all()
        )

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == current_user.location_id,
                )
            )
        ).scalar_one_or_none()
        location_name = location.name if location else None

        return [
            InstitutionUserRowResponse(
                id=str(u.id),
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                invite_status=u.invite_status,
                institution_id=str(u.institution_id) if u.institution_id else None,
                location_id=str(u.location_id) if u.location_id else None,
                location_name=location_name,
            )
            for u in users
        ]


@router.post("/location/users/{user_id}/deactivate", response_model=UserActionResponse)
async def deactivate_location_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_location_admin)],
):
    """
    Location admin: deactivate a STAFF user at their own location.
    """
    if not current_user.institution_id or not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment"
        )
    if str(current_user.id) == str(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    async with get_db_session() as session:
        target = (
            await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.institution_id == current_user.institution_id,
                    User.location_id == current_user.location_id,
                    User.role == UserRole.STAFF.value,
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff user not found at your location",
            )

        target.is_active = False

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.LOCATION_USER_DELETE,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "institution_id": current_user.institution_id,
            "location_id": current_user.location_id,
            "deactivated_user_id": user_id,
        },
        institution_id=current_user.institution_id,
    )
    return UserActionResponse(message="Staff user deactivated", user_id=user_id)


@router.post(
    "/locations/{loc_slug}/invite-location-admin", status_code=status.HTTP_201_CREATED
)
async def invite_location_admin(
    loc_slug: str,
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )
    async with get_db_session() as session:
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invite did not return user id",
            )

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.LOCATION_ADMIN.value,
            institution_id=current_user.institution_id,
            location_id=location.id,
            invite_status=InviteStatus.PENDING.value,
            is_active=True,
        )
        session.add(user)
        apply_invite_cooldown(actor)

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment"
        )
    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug)
        if not location or location.institution_id != current_user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        if str(location.id) != str(current_user.location_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only invite staff for your location",
            )

        existing = await session.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invite did not return user id",
            )

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.STAFF.value,
            institution_id=current_user.institution_id,
            location_id=location.id,
            invite_status=InviteStatus.PENDING.value,
            is_active=True,
        )
        session.add(user)
        apply_invite_cooldown(actor)

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


# =============================================================================
# ROI Configuration & Calculation
# =============================================================================


# ── Billing Email ─────────────────────────────────────────────────────────────


class BillingEmailRequest(BaseModel):
    billing_email: str = Field(
        ..., max_length=255, description="Email address for invoices"
    )


class BillingEmailResponse(BaseModel):
    billing_email: str | None


@router.get("/billing-email", response_model=BillingEmailResponse)
async def get_billing_email(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
            )
        return BillingEmailResponse(billing_email=institution.billing_email)


@router.put("/billing-email", response_model=BillingEmailResponse)
async def update_billing_email(
    data: BillingEmailRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
            )
        institution.billing_email = data.billing_email

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.INSTITUTION_UPDATE,
        target_resource="institution:billing_email",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role, "billing_email": data.billing_email},
        institution_id=current_user.institution_id,
    )
    return BillingEmailResponse(billing_email=data.billing_email)


# ── ROI Configuration ────────────────────────────────────────────────────────


class ROIConfigRequest(BaseModel):
    avg_appointment_value: float = Field(
        ..., ge=0, description="Average appointment revenue ($)"
    )
    avg_new_patient_value: float = Field(
        ..., ge=0, description="Average new patient first-visit revenue ($)"
    )
    monthly_subscription_cost: float = Field(
        ..., ge=0, description="Monthly Nexus subscription cost ($)"
    )
    staff_hourly_rate: float = Field(
        ..., ge=0, description="Front desk staff hourly rate ($)"
    )
    avg_call_duration_minutes: float = Field(
        4.0, ge=0, description="Avg manual call handling time (minutes)"
    )


class ROIConfigResponse(BaseModel):
    avg_appointment_value: float
    avg_new_patient_value: float
    monthly_subscription_cost: float
    staff_hourly_rate: float
    avg_call_duration_minutes: float


class ROICalculationResponse(BaseModel):
    # Inputs used
    config: ROIConfigResponse
    # Raw metrics
    total_calls_month: int
    appointments_booked_month: int
    new_patients_month: int
    # Calculated values
    revenue_from_bookings: float
    revenue_from_new_patients: float
    total_revenue_generated: float
    staff_time_saved_hours: float
    staff_cost_saved: float
    total_value: float
    monthly_cost: float
    net_value: float
    roi_percentage: float


@router.get("/roi/config", response_model=ROIConfigResponse | None)
async def get_roi_config(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution or not institution.roi_config:
            return None
        return ROIConfigResponse(**institution.roi_config)


@router.put("/roi/config", response_model=ROIConfigResponse)
async def update_roi_config(
    data: ROIConfigRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    config_dict = data.model_dump()

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
            )
        institution.roi_config = config_dict

    log_audit_background(
        actor=current_user.id,
        action=AuditAction.INSTITUTION_UPDATE,
        target_resource="institution:roi_config",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role, "config": config_dict},
        institution_id=current_user.institution_id,
    )
    return ROIConfigResponse(**config_dict)


@router.get("/roi/calculate", response_model=ROICalculationResponse)
async def calculate_roi(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    from datetime import date, datetime, timezone as tz
    from src.app.models.call import Call, CallStatus

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution or not institution.roi_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ROI configuration not set. Please configure ROI settings first.",
            )

        config = ROIConfigResponse(**institution.roi_config)
        institution_id = current_user.institution_id
        today = datetime.now(tz.utc).date()
        month_start = today.replace(day=1)

        total_calls_month = (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.institution_id == institution_id,
                    Call.call_date >= month_start,
                )
            )
        ).scalar_one()

        appointments_booked_month = (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.institution_id == institution_id,
                    Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                    Call.call_date >= month_start,
                )
            )
        ).scalar_one()

        new_patients_month = (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.institution_id == institution_id,
                    Call.is_new_patient.is_(True),
                    Call.call_date >= month_start,
                )
            )
        ).scalar_one()

    # Calculate ROI
    revenue_from_bookings = appointments_booked_month * config.avg_appointment_value
    revenue_from_new_patients = new_patients_month * config.avg_new_patient_value
    total_revenue_generated = revenue_from_bookings + revenue_from_new_patients

    staff_time_saved_hours = round(
        (total_calls_month * config.avg_call_duration_minutes) / 60, 2
    )
    staff_cost_saved = round(staff_time_saved_hours * config.staff_hourly_rate, 2)

    total_value = round(total_revenue_generated + staff_cost_saved, 2)
    monthly_cost = config.monthly_subscription_cost
    net_value = round(total_value - monthly_cost, 2)
    roi_percentage = (
        round((net_value / monthly_cost) * 100, 2) if monthly_cost > 0 else 0.0
    )

    return ROICalculationResponse(
        config=config,
        total_calls_month=total_calls_month,
        appointments_booked_month=appointments_booked_month,
        new_patients_month=new_patients_month,
        revenue_from_bookings=round(revenue_from_bookings, 2),
        revenue_from_new_patients=round(revenue_from_new_patients, 2),
        total_revenue_generated=round(total_revenue_generated, 2),
        staff_time_saved_hours=staff_time_saved_hours,
        staff_cost_saved=staff_cost_saved,
        total_value=total_value,
        monthly_cost=monthly_cost,
        net_value=net_value,
        roi_percentage=roi_percentage,
    )


@router.get("/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_my_audit_logs(
    current_user: Annotated[User, Depends(get_current_institution_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
):
    """
    Institution admin: view all audit logs for the institution including sub-locations.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
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
            pages=pages,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment"
        )

    location_id = str(current_user.location_id)
    async with get_db_session() as session:
        filter_expr = AuditLog.audit_metadata["location_id"].as_string() == location_id
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


# =============================================================================
# Insurance Plans
# =============================================================================


class InsurancePlanRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)


class InsurancePlanResponse(BaseModel):
    id: str
    location_id: str
    name: str
    description: str | None
    is_active: bool


@router.get(
    "/locations/{loc_slug}/insurance-plans", response_model=list[InsurancePlanResponse]
)
async def list_insurance_plans(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """List insurance plans for a location. All institution-scoped roles can view."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        plans = (
            (
                await session.execute(
                    select(InsurancePlan)
                    .where(
                        InsurancePlan.location_id == str(location.id),
                        InsurancePlan.institution_id == current_user.institution_id,
                        InsurancePlan.is_active == True,
                    )
                    .order_by(InsurancePlan.name)
                )
            )
            .scalars()
            .all()
        )

        return [
            InsurancePlanResponse(
                id=str(p.id),
                location_id=str(p.location_id),
                name=p.name,
                description=p.description,
                is_active=p.is_active,
            )
            for p in plans
        ]


@router.post(
    "/locations/{loc_slug}/insurance-plans",
    response_model=InsurancePlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_insurance_plan(
    loc_slug: str,
    data: InsurancePlanRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Create an insurance plan. INSTITUTION_ADMIN or LOCATION_ADMIN only."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        plan = InsurancePlan(
            institution_id=current_user.institution_id,
            location_id=str(location.id),
            name=data.name,
            description=data.description,
        )
        session.add(plan)
        await session.flush()

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/insurance_plan:{plan.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "create_insurance_plan",
                "plan_name": data.name,
            },
            institution_id=current_user.institution_id,
        )

        return InsurancePlanResponse(
            id=str(plan.id),
            location_id=str(plan.location_id),
            name=plan.name,
            description=plan.description,
            is_active=plan.is_active,
        )


@router.patch(
    "/locations/{loc_slug}/insurance-plans/{plan_id}",
    response_model=InsurancePlanResponse,
)
async def update_insurance_plan(
    loc_slug: str,
    plan_id: str,
    data: InsurancePlanRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Update an insurance plan. INSTITUTION_ADMIN or LOCATION_ADMIN only."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        plan = (
            await session.execute(
                select(InsurancePlan).where(
                    InsurancePlan.id == plan_id,
                    InsurancePlan.location_id == str(location.id),
                    InsurancePlan.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Insurance plan not found"
            )

        plan.name = data.name
        plan.description = data.description

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/insurance_plan:{plan_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "update_insurance_plan",
                "plan_name": data.name,
            },
            institution_id=current_user.institution_id,
        )

        return InsurancePlanResponse(
            id=str(plan.id),
            location_id=str(plan.location_id),
            name=plan.name,
            description=plan.description,
            is_active=plan.is_active,
        )


@router.delete(
    "/locations/{loc_slug}/insurance-plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_insurance_plan(
    loc_slug: str,
    plan_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Soft-delete an insurance plan. INSTITUTION_ADMIN or LOCATION_ADMIN only."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    async with get_db_session() as session:
        from src.app.models.institution_location import InstitutionLocation

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == loc_slug,
                    InstitutionLocation.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        if current_user.role == UserRole.LOCATION_ADMIN.value:
            if str(location.id) != str(current_user.location_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this location",
                )

        plan = (
            await session.execute(
                select(InsurancePlan).where(
                    InsurancePlan.id == plan_id,
                    InsurancePlan.location_id == str(location.id),
                    InsurancePlan.institution_id == current_user.institution_id,
                )
            )
        ).scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Insurance plan not found"
            )

        plan.is_active = False

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"location:{loc_slug}/insurance_plan:{plan_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "action": "delete_insurance_plan",
                "plan_name": plan.name,
            },
            institution_id=current_user.institution_id,
        )
