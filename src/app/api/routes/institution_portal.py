"""
Institution portal routes.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import time as dt_time
import re
from typing import Annotated, Any, Literal
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
from src.app.api.deps_scope import require_location_scope
from src.app.api.models import AuditLogPaginatedResponse, AuditLogResponse
from src.app.database import get_db_session
from src.app.models.user import User, UserRole
from src.app.models.audit_log import AuditLog
from src.app.models.institution_location import InstitutionLocation
from src.app.models.insurance_plan import InsurancePlan
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.services.institution_service import InstitutionService
from src.app.services.invite_cooldown import apply_invite_cooldown, ensure_invite_cooldown
from src.app.services.user_invite_service import UserInviteService
from src.app.services.audit import log_audit, log_audit_background
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit_decorator import audit

router = APIRouter(prefix="/institution", tags=["Institution Portal"])


class InstitutionPortalMeResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: str
    institution_id: str | None
    location_id: str | None
    # PMS integration mode for this tenant. The frontend gates Practice Setup
    # nav/routes on has_pms (no-PMS tenants are call-intelligence-only).
    pms_type: str = "nexhealth"
    has_pms: bool = True


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


class BreakCreateRequest(BaseModel):
    """A recurring break window (e.g. lunch) to hide from bookable slots."""

    name: str = Field(..., min_length=1, max_length=100)
    day_of_week: int | None = Field(None, ge=0, le=6, description="NULL = every day")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")


class BreakResponse(BaseModel):
    id: str
    location_id: str
    name: str
    day_of_week: int | None = None
    start_time: str
    end_time: str

    @classmethod
    def from_model(cls, m: Any) -> "BreakResponse":
        return cls(
            id=str(m.id),
            location_id=str(m.location_id),
            name=m.name,
            day_of_week=m.day_of_week,
            start_time=m.start_time.strftime("%H:%M"),
            end_time=m.end_time.strftime("%H:%M"),
        )


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
        user_id=str(item.user_id) if item.user_id else None,
        location_id=str(item.location_id) if item.location_id else None,
        actor=actor,
        action=item.action,
        target_resource=_sanitize_target_resource(item.target_resource),
        outcome=item.outcome,
        audit_metadata=_sanitize_audit_metadata(item.audit_metadata),
    )


def _validate_invite_role(role: str) -> str:
    normalized = role.strip().upper()
    # STAFF is a valid institution role — the /users/invite endpoint already
    # requires + resolves a location for LOCATION_ADMIN and STAFF alike, and the
    # UI offers all three. (SUPER_ADMIN is intentionally excluded.)
    allowed = {
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
        UserRole.STAFF.value,
    }
    if normalized not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{role}'. Allowed: {', '.join(sorted(allowed))}",
        )
    return normalized


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
        pms_type=getattr(institution, "pms_type", "nexhealth") or "nexhealth",
        has_pms=getattr(institution, "has_pms", True),
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
    "/locations/{loc_slug}/operating-hours",
    response_model=list[OperatingHoursResponse],
    dependencies=[Depends(require_location_scope())],
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
        await ensure_invite_cooldown(session, current_user)
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        result = await session.execute(
            select(LocationOperatingHours)
            .where(LocationOperatingHours.location_id == location.id)
            .order_by(LocationOperatingHours.day_of_week)
        )
        return [OperatingHoursResponse.from_model(h) for h in result.scalars().all()]


@router.put(
    "/locations/{loc_slug}/operating-hours",
    response_model=list[OperatingHoursResponse],
    dependencies=[Depends(require_location_scope())],
)
@audit(
    AuditAction.LOCATION_UPDATE,
    # Item 39: operating hours and breaks decide when a patient may be
    # contacted at all — they are the source quiet hours is derived from — so
    # changing them is the same class of act as changing a campaign.
    resource=lambda *args, **kwargs: (
        f"location:{kwargs.get('loc_slug') or 'unknown'}:operating_hours"
    ),
    actor=AuditActor.ADMIN,
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
        location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
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


# =============================================================================
# Breaks (recurring blackout windows, e.g. lunch) — institution/location admin
# =============================================================================


async def _resolve_scoped_location(session, current_user: User, loc_slug: str):
    """Resolve a location by slug within the caller's institution, or 404."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )
    location = await InstitutionService(session).get_location_by_slug(
        loc_slug, current_user.institution_id
    )
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


@router.get(
    "/locations/{loc_slug}/breaks",
    response_model=list[BreakResponse],
    dependencies=[Depends(require_location_scope())],
)
async def get_location_breaks(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    async with get_db_session() as session:
        location = await _resolve_scoped_location(session, current_user, loc_slug)
        result = await session.execute(
            select(LocationBreak)
            .where(LocationBreak.location_id == location.id)
            .order_by(LocationBreak.day_of_week.nulls_first(), LocationBreak.start_time)
        )
        return [BreakResponse.from_model(b) for b in result.scalars().all()]


@router.post(
    "/locations/{loc_slug}/breaks",
    response_model=BreakResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_location_scope())],
)
@audit(
    AuditAction.LOCATION_UPDATE,
    # Item 39: operating hours and breaks decide when a patient may be
    # contacted at all — they are the source quiet hours is derived from — so
    # changing them is the same class of act as changing a campaign.
    resource=lambda *args, **kwargs: (
        f"location:{kwargs.get('loc_slug') or 'unknown'}:break_create"
    ),
    actor=AuditActor.ADMIN,
)
async def create_location_break(
    loc_slug: str,
    data: BreakCreateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """Add a recurring break (e.g. lunch). Staff cannot edit."""
    if current_user.role == UserRole.STAFF.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Staff cannot edit breaks"
        )
    start = dt_time.fromisoformat(data.start_time)
    end = dt_time.fromisoformat(data.end_time)
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    async with get_db_session() as session:
        location = await _resolve_scoped_location(session, current_user, loc_slug)
        brk = LocationBreak(
            location_id=location.id,
            name=data.name,
            day_of_week=data.day_of_week,
            start_time=start,
            end_time=end,
        )
        session.add(brk)
        await session.flush()
        return BreakResponse.from_model(brk)


@router.delete(
    "/locations/{loc_slug}/breaks/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_location_scope())],
)
@audit(
    AuditAction.LOCATION_UPDATE,
    # Item 39: operating hours and breaks decide when a patient may be
    # contacted at all — they are the source quiet hours is derived from — so
    # changing them is the same class of act as changing a campaign.
    resource=lambda *args, **kwargs: (
        f"location:{kwargs.get('loc_slug') or 'unknown'}:break_delete"
    ),
    actor=AuditActor.ADMIN,
)
async def delete_location_break(
    loc_slug: str,
    break_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
):
    """Remove a break. Staff cannot edit."""
    if current_user.role == UserRole.STAFF.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Staff cannot edit breaks"
        )
    async with get_db_session() as session:
        location = await _resolve_scoped_location(session, current_user, loc_slug)
        # Scope the delete to this location so a caller can't remove another
        # location's break by guessing an id.
        result = await session.execute(
            delete(LocationBreak).where(
                LocationBreak.id == break_id,
                LocationBreak.location_id == location.id,
            )
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Break not found"
            )
    return None


@router.patch(
    "/locations/{loc_slug}/timezone",
    response_model=InstitutionLocationLiteResponse,
    dependencies=[Depends(require_location_scope())],
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
        location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        location.timezone = timezone_value
        await session.flush()

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
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
    dependencies=[Depends(require_location_scope())],
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

        entry = InstitutionLocationTransferNumber(
            institution_id=current_user.institution_id,
            location_id=str(location.id),
            phone_number=data.phone_number,
            department=data.department,
        )
        session.add(entry)
        await session.flush()

        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
    dependencies=[Depends(require_location_scope())],
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
    dependencies=[Depends(require_location_scope())],
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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

    email = UserInviteService.normalize_email(data.email)
    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        existing = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

        invite_service = UserInviteService(session)
        try:
            await invite_service.create_invited_user(
                email=email,
                institution_id=current_user.institution_id,
                role=UserRole.INSTITUTION_ADMIN.value,
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )
        apply_invite_cooldown(actor)

    await log_audit(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"user:{email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.INSTITUTION_ADMIN.value,
            "institution_id": current_user.institution_id,
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Institution admin invite sent to {email}"}


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
                        User.deleted_at.is_(None),
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
    - LOCATION_ADMIN / STAFF: location_slug required
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    email = UserInviteService.normalize_email(data.email)
    role = _validate_invite_role(data.role)

    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        from src.app.models.institution_location import InstitutionLocation

        existing = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

        location_id: str | None = None
        if role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
            if not data.location_slug:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"location_slug is required for {role}",
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

        invite_service = UserInviteService(session)
        try:
            created = await invite_service.create_invited_user(
                email=email,
                institution_id=current_user.institution_id,
                role=role,
                location_id=location_id,
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )
        apply_invite_cooldown(actor)

    await log_audit(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"user:{email}",
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
        message=f"Invite sent to {email}", user_id=str(created.id)
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
                    User.deleted_at.is_(None),
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

        target.mark_deleted()

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.LOCATION_USER_DELETE,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "institution_id": current_user.institution_id,
            "deactivated_user_id": user_id,
        },
        institution_id=current_user.institution_id,
        user_id=str(current_user.id),
    )
    return UserActionResponse(message="User deactivated", user_id=user_id)


@router.post("/users/{user_id}/reinvite", response_model=UserActionResponse)
async def reinvite_institution_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
):
    """
    Reinvite an institution-scoped user by rotating their local invite token.
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
                    User.deleted_at.is_(None),
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
        invite_service = UserInviteService(session)
        try:
            await invite_service.reinvite_user(target)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )
        apply_invite_cooldown(actor)

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.USER_REINVITED,
        target_resource=f"user:{old_email}:reinvite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "institution_id": current_user.institution_id,
            "old_user_id": old_user_id,
            "new_user_id": old_user_id,
            "role": old_role,
            "location_id": old_location_id,
        },
        institution_id=current_user.institution_id,
        user_id=str(current_user.id),
        location_id=old_location_id,
    )
    return UserActionResponse(
        message=f"Reinvite sent to {old_email}", user_id=old_user_id
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
                        User.deleted_at.is_(None),
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
                    User.deleted_at.is_(None),
                    User.role == UserRole.STAFF.value,
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff user not found at your location",
            )

        target.mark_deleted()

    await log_audit(
        actor=AuditActor.ADMIN,
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
        user_id=str(current_user.id),
        location_id=str(current_user.location_id),
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
    email = UserInviteService.normalize_email(data.email)
    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        existing = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

        invite_service = UserInviteService(session)
        try:
            await invite_service.create_invited_user(
                email=email,
                institution_id=current_user.institution_id,
                role=UserRole.LOCATION_ADMIN.value,
                location_id=str(location.id),
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )
        apply_invite_cooldown(actor)

    await log_audit(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"location:{loc_slug}/user:{email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.LOCATION_ADMIN.value,
            "institution_id": current_user.institution_id,
            "location_id": str(location.id),
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Location admin invite sent to {email}"}


@router.post(
    "/locations/{loc_slug}/invite-staff",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_location_scope())],
)
async def invite_staff(
    loc_slug: str,
    data: InviteUserRequest,
    current_user: Annotated[User, Depends(get_current_location_admin)],
):
    if not current_user.institution_id or not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No location assignment"
        )
    email = UserInviteService.normalize_email(data.email)
    async with get_db_session() as session:
        actor = await ensure_invite_cooldown(session, current_user)
        svc = InstitutionService(session)
        location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        existing = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )

        invite_service = UserInviteService(session)
        try:
            await invite_service.create_invited_user(
                email=email,
                institution_id=current_user.institution_id,
                role=UserRole.STAFF.value,
                location_id=str(location.id),
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )
        apply_invite_cooldown(actor)

    await log_audit(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_USER_CREATE,
        target_resource=f"location:{loc_slug}/staff:{email}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "created_role": UserRole.STAFF.value,
            "institution_id": current_user.institution_id,
            "location_id": str(location.id),
        },
        institution_id=current_user.institution_id,
    )
    return {"message": f"Staff invite sent to {email}"}


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
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
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
    #: How this tenant is billed. "institution" charges once for the group and
    #: apportions it across locations; "location" charges each clinic its own
    #: price and ignores monthly_subscription_cost above. Both are real deals,
    #: so neither is hard-coded.
    subscription_billing_mode: Literal["institution", "location"] = Field(
        "institution", description="Whether the subscription is billed per institution or per location"
    )


class ROIConfigResponse(BaseModel):
    avg_appointment_value: float
    avg_new_patient_value: float
    monthly_subscription_cost: float
    staff_hourly_rate: float
    avg_call_duration_minutes: float
    subscription_billing_mode: str = "institution"


class ROICalculationResponse(BaseModel):
    # Inputs used. None when the figures were summed from locations that each
    # have their own — there is no single set of inputs to name.
    config: ROIConfigResponse | None
    #: The window these figures cover.
    period_start: date_type | None = None
    period_end: date_type | None = None
    # Raw metrics
    total_calls_month: int
    appointments_booked_month: int
    new_patients_month: int
    # Calculated values
    revenue_from_bookings: float
    revenue_from_new_patients: float
    total_revenue_generated: float
    staff_time_saved_hours: float
    #: None when no hourly rate is configured — unknown, not zero.
    staff_cost_saved: float | None
    total_value: float
    monthly_cost: float
    net_value: float
    #: None when there is no cost to measure a return against.
    roi_percentage: float | None
    #: Where these figures came from, so a summed total is never mistaken for
    #: one the institution itself was configured with.
    revenue_basis: str = "Institution-wide figures"


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
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
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
    start_date: date_type | None = Query(
        None, description="Inclusive window start (YYYY-MM-DD). Defaults to the 1st."
    ),
    end_date: date_type | None = Query(
        None, description="Inclusive window end (YYYY-MM-DD). Defaults to today."
    ),
):
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )

    from src.app.models.call import Call, CallStatus

    period_start, period_end = _roi_window(start_date, end_date)

    async with get_db_session() as session:
        svc = InstitutionService(session)
        institution = await svc.get_by_id(current_user.institution_id)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
            )
        if not institution.roi_config:
            # No tenant-wide figures, but its clinics may each have their own.
            # A group's revenue *is* the sum of its locations', so sum them
            # rather than refusing: a two-clinic group that priced both of them
            # has answered the question, just not in one place.
            return await _aggregated_institution_roi(
                session,
                institution=institution,
                institution_id=str(current_user.institution_id),
                period_start=period_start,
                period_end=period_end,
            )

        config = ROIConfigResponse(**institution.roi_config)
        institution_id = current_user.institution_id

        def _count(*extra):
            return select(func.count(Call.id)).where(
                Call.institution_id == institution_id,
                Call.call_date >= period_start,
                Call.call_date <= period_end,
                *extra,
            )

        total_calls_month = (await session.execute(_count())).scalar_one()
        appointments_booked_month = (
            await session.execute(
                _count(Call.call_status == CallStatus.APPOINTMENT_BOOKED.value)
            )
        ).scalar_one()
        new_patients_month = (
            await session.execute(_count(Call.is_new_patient.is_(True)))
        ).scalar_one()

        billing_mode = _billing_mode(institution)
        location_subscription_costs: list[float] = []
        if billing_mode == "location":
            rows = await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.institution_id == institution_id
                )
            )
            location_subscription_costs = [
                cost
                for cost in (
                    _location_subscription_cost(row) for row in rows.scalars().all()
                )
                if cost is not None
            ]

    # Calculate ROI
    revenue_from_bookings = appointments_booked_month * config.avg_appointment_value
    revenue_from_new_patients = new_patients_month * config.avg_new_patient_value
    total_revenue_generated = revenue_from_bookings + revenue_from_new_patients

    staff_time_saved_hours = round(
        (total_calls_month * config.avg_call_duration_minutes) / 60, 2
    )
    staff_cost_saved = round(staff_time_saved_hours * config.staff_hourly_rate, 2)

    total_value = round(total_revenue_generated + staff_cost_saved, 2)
    # Under per-location billing the group's monthly cost is what its clinics
    # are charged, not the institution field — which is the other model's price
    # and is left in place so switching back does not lose it. Reading it here
    # would make this page disagree with the sum of the location pages.
    monthly_cost = (
        round(sum(location_subscription_costs), 2)
        if billing_mode == "location"
        else config.monthly_subscription_cost
    )
    net_value = round(total_value - monthly_cost, 2)
    roi_percentage = (
        round((net_value / monthly_cost) * 100, 2) if monthly_cost > 0 else None
    )

    return ROICalculationResponse(
        config=config,
        period_start=period_start,
        period_end=period_end,
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


async def _aggregated_institution_roi(
    session,
    *,
    institution: Any,
    institution_id: str,
    period_start: Any,
    period_end: Any,
) -> "ROICalculationResponse":
    """Institution totals summed from the locations that carry their own figures.

    Each location is valued with its own numbers rather than an average of
    them, because that is the only way a group whose clinics bill differently
    gets a total that matches the sum of its location pages.

    Counts are restricted to the contributing locations. Reporting the group's
    whole call volume beside revenue earned by a subset would put a booking
    rate and a revenue figure side by side that were measured over different
    sets of clinics.
    """
    from src.app.models.call import Call, CallStatus

    rows = await session.execute(
        select(InstitutionLocation).where(
            InstitutionLocation.institution_id == institution_id
        )
    )
    locations = list(rows.scalars().all())

    contributing: list[Any] = []
    totals = {
        "calls": 0,
        "booked": 0,
        "new_patients": 0,
        "revenue_bookings": 0.0,
        "revenue_new_patients": 0.0,
        "staff_hours": 0.0,
        "staff_cost": 0.0,
    }
    #: None until some location supplies a rate; stays None when none do, so an
    #: unmeasured saving is not reported as a saving of nothing.
    staff_cost: float | None = None

    for location in locations:
        resolved = _resolved_location_roi(location, institution)
        if resolved is None:
            continue
        values, _ = resolved
        contributing.append(location)

        def _count(*extra):
            return select(func.count(Call.id)).where(
                Call.institution_id == institution_id,
                Call.location_id == str(location.id),
                Call.call_date >= period_start,
                Call.call_date <= period_end,
                *extra,
            )

        calls = (await session.execute(_count())).scalar_one()
        booked = (
            await session.execute(
                _count(Call.call_status == CallStatus.APPOINTMENT_BOOKED.value)
            )
        ).scalar_one()
        new_patients = (
            await session.execute(_count(Call.is_new_patient.is_(True)))
        ).scalar_one()

        hours = (calls * (values["avg_call_duration_minutes"] or 0.0)) / 60
        totals["calls"] += calls
        totals["booked"] += booked
        totals["new_patients"] += new_patients
        totals["revenue_bookings"] += booked * values["avg_appointment_value"]
        totals["revenue_new_patients"] += new_patients * values["avg_new_patient_value"]
        totals["staff_hours"] += hours
        rate = values["staff_hourly_rate"]
        if rate is not None:
            staff_cost = (staff_cost or 0.0) + hours * rate

    if not contributing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "ROI configuration not set for this institution or any of its "
                "locations. Please configure ROI settings first."
            ),
        )

    total_revenue = totals["revenue_bookings"] + totals["revenue_new_patients"]
    staff_cost = None if staff_cost is None else round(staff_cost, 2)
    total_value = round(total_revenue + (staff_cost or 0.0), 2)

    # With no institution-level config there is no institution-level price, so
    # a cost only exists where the clinics are billed individually.
    monthly_cost = round(
        sum(
            cost
            for cost in (_location_subscription_cost(loc) for loc in contributing)
            if cost is not None
        ),
        2,
    )
    net_value = round(total_value - monthly_cost, 2)

    names = ", ".join(sorted(str(loc.name) for loc in contributing))
    return ROICalculationResponse(
        config=None,
        period_start=period_start,
        period_end=period_end,
        total_calls_month=totals["calls"],
        appointments_booked_month=totals["booked"],
        new_patients_month=totals["new_patients"],
        revenue_from_bookings=round(totals["revenue_bookings"], 2),
        revenue_from_new_patients=round(totals["revenue_new_patients"], 2),
        total_revenue_generated=round(total_revenue, 2),
        staff_time_saved_hours=round(totals["staff_hours"], 2),
        staff_cost_saved=staff_cost,
        total_value=total_value,
        monthly_cost=monthly_cost,
        net_value=net_value,
        roi_percentage=(
            round((net_value / monthly_cost) * 100, 2) if monthly_cost > 0 else None
        ),
        revenue_basis=(
            f"Summed from {len(contributing)} location"
            f"{'' if len(contributing) == 1 else 's'} with their own figures: {names}"
        ),
    )


# ── Per-location ROI ─────────────────────────────────────────────────────────
#
# The institution-level inputs above assume one set of economics per tenant. A
# group whose downtown and suburban practices bill differently had to pick one
# average appointment value for both, so a per-location number was worth more
# than the average of the two.
#
# Subscription cost is charged both ways depending on the deal, so the shape has
# to carry both rather than pick one. `subscription_billing_mode` on the
# institution decides which, and it is stated rather than inferred from whether
# a location happens to have a price on it: a tenant halfway through being moved
# from one billing model to the other would otherwise produce a silent mix of
# apportioned and direct costs that reconciles against no invoice.


class LocationROIConfigRequest(BaseModel):
    """Per-location value inputs, including a per-location subscription price."""

    avg_appointment_value: float = Field(
        ..., ge=0, description="Average appointment revenue at this location ($)"
    )
    avg_new_patient_value: float = Field(
        ..., ge=0, description="Average new patient first-visit revenue here ($)"
    )
    #: Optional. Clinics that do not track a front desk rate leave it blank,
    #: and the staff-time saving is then reported as unknown rather than as a
    #: saving of zero — "not measured" and "saved nothing" are different claims.
    staff_hourly_rate: float | None = Field(
        None, ge=0, description="Front desk staff hourly rate at this location ($)"
    )
    avg_call_duration_minutes: float = Field(
        4.0, ge=0, description="Avg manual call handling time (minutes)"
    )
    #: What this location is billed per month. Only consulted when the
    #: institution bills per location; ignored (but kept) otherwise, so
    #: switching billing mode does not destroy the other mode's numbers.
    monthly_subscription_cost: float | None = Field(
        None, ge=0, description="Monthly subscription for this location ($)"
    )


class LocationROIConfigResponse(BaseModel):
    location_id: str
    location_slug: str
    avg_appointment_value: float
    avg_new_patient_value: float
    staff_hourly_rate: float | None
    avg_call_duration_minutes: float
    #: None when this location has no price of its own. Meaningful only under
    #: per-location billing; under per-institution billing it stays None and the
    #: institution's cost is apportioned instead.
    monthly_subscription_cost: float | None
    #: "institution" or "location" — how this tenant is billed, so a reader can
    #: tell an apportioned cost from a directly billed one.
    subscription_billing_mode: str
    #: "location" when these numbers were set here, "institution" when the
    #: location has none of its own and the tenant-wide ones are standing in.
    #: Reported rather than smoothed over: a clinic reading a group average as
    #: its own performance is the failure this field exists to prevent.
    source: str


class LocationROICalculationResponse(BaseModel):
    config: LocationROIConfigResponse
    #: The window these figures cover. Explicit because the caller can pick it,
    #: and a revenue number without its period is unreadable.
    period_start: date_type
    period_end: date_type
    total_calls_month: int
    appointments_booked_month: int
    new_patients_month: int
    revenue_from_bookings: float
    revenue_from_new_patients: float
    total_revenue_generated: float
    staff_time_saved_hours: float
    #: None when no hourly rate is configured — unknown, not zero.
    staff_cost_saved: float | None
    total_value: float
    #: This location's monthly subscription — its own price under per-location
    #: billing, the institution's apportioned share under per-institution.
    monthly_cost_allocated: float
    #: How that figure was reached, so it can be checked against an invoice.
    cost_allocation_basis: str
    net_value: float
    #: None when there is no cost to measure a return against. Reporting 0.0
    #: there would read as a 0% return rather than an unanswerable question.
    roi_percentage: float | None


#: Inputs a location inherits from its institution when it has none of its own.
#: Subscription cost is absent on purpose — see _location_subscription_cost.
_LOCATION_ROI_FIELDS = (
    "avg_appointment_value",
    "avg_new_patient_value",
    "staff_hourly_rate",
    "avg_call_duration_minutes",
)


def _roi_window(start_date: Any, end_date: Any) -> tuple[Any, Any]:
    """Resolve the reporting window, defaulting to calendar month-to-date.

    The dashboard drives this from the same picker as its call cards, so a
    revenue figure can never describe a different period from the counts it
    sits beside — which is precisely the confusion the fixed-month KPI row on
    that page caused before it was removed.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime
    from datetime import timezone as _tz

    today = _datetime.now(_tz.utc).date()
    end = end_date or today
    if end > today:  # no data in the future
        end = today
    start = start_date or end.replace(day=1)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )
    assert isinstance(start, _date) and isinstance(end, _date)
    return start, end


def _billing_mode(institution: Any) -> str:
    """Whether this tenant is billed per institution or per location."""
    raw = institution.roi_config if isinstance(institution.roi_config, dict) else {}
    mode = raw.get("subscription_billing_mode")
    return mode if mode in ("institution", "location") else "institution"


def _location_subscription_cost(location: Any) -> float | None:
    """This location's own monthly price, or None if it has not been set.

    Never inherited from the institution. The institution's figure is the price
    of the whole group; charging it to a single clinic as though it were that
    clinic's own would overstate cost by the number of locations.
    """
    raw = location.roi_config if isinstance(location.roi_config, dict) else {}
    value = raw.get("monthly_subscription_cost")
    return None if value is None else float(value)


def _resolved_location_roi(
    location: Any, institution: Any
) -> tuple[dict[str, float | None], str] | None:
    """This location's inputs and where they came from, or None if unset.

    Falls through to the institution only as a whole: mixing a location's
    appointment value with the institution's hourly rate would produce a figure
    that is neither, and no caller could tell which parts were which.
    """
    if isinstance(location.roi_config, dict) and location.roi_config:
        raw, source = location.roi_config, "location"
    elif isinstance(institution.roi_config, dict) and institution.roi_config:
        raw, source = institution.roi_config, "institution"
    else:
        return None
    values: dict[str, float | None] = {
        key: float(raw.get(key) or 0.0) for key in _LOCATION_ROI_FIELDS
    }
    # Distinguish "no rate configured" from "a rate of zero": the first makes
    # the staff saving unknown, the second makes it genuinely nil.
    rate = raw.get("staff_hourly_rate")
    values["staff_hourly_rate"] = None if rate is None else float(rate)
    return values, source


async def _location_for_roi(session, loc_slug: str, current_user: User):
    svc = InstitutionService(session)
    location = await svc.get_location_by_slug(loc_slug, current_user.institution_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    institution = await svc.get_by_id(current_user.institution_id)
    if not institution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
        )
    return location, institution


def _require_institution(current_user: User) -> str:
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution assignment"
        )
    return str(current_user.institution_id)


@router.get(
    "/locations/{loc_slug}/roi/config",
    response_model=LocationROIConfigResponse | None,
    dependencies=[Depends(require_location_scope())],
)
async def get_location_roi_config(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    _require_institution(current_user)
    async with get_db_session() as session:
        location, institution = await _location_for_roi(
            session, loc_slug, current_user
        )
        resolved = _resolved_location_roi(location, institution)
        if resolved is None:
            return None
        values, source = resolved
        return LocationROIConfigResponse(
            location_id=str(location.id),
            location_slug=location.slug,
            source=source,
            monthly_subscription_cost=_location_subscription_cost(location),
            subscription_billing_mode=_billing_mode(institution),
            **values,
        )


@router.put(
    "/locations/{loc_slug}/roi/config",
    response_model=LocationROIConfigResponse,
    dependencies=[Depends(require_location_scope())],
)
@audit(
    AuditAction.LOCATION_UPDATE,
    resource=lambda *args, **kwargs: (
        f"location:{kwargs.get('loc_slug') or 'unknown'}:roi_config"
    ),
    actor=AuditActor.ADMIN,
)
async def update_location_roi_config(
    loc_slug: str,
    data: LocationROIConfigRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    _require_institution(current_user)
    config_dict = data.model_dump()
    async with get_db_session() as session:
        location, institution = await _location_for_roi(
            session, loc_slug, current_user
        )
        location.roi_config = config_dict
        location_id = str(location.id)
        slug = location.slug
        mode = _billing_mode(institution)

    return LocationROIConfigResponse(
        location_id=location_id,
        location_slug=slug,
        source="location",
        subscription_billing_mode=mode,
        **config_dict,
    )


@router.delete(
    "/locations/{loc_slug}/roi/config",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_location_scope())],
)
@audit(
    AuditAction.LOCATION_UPDATE,
    resource=lambda *args, **kwargs: (
        f"location:{kwargs.get('loc_slug') or 'unknown'}:roi_config"
    ),
    actor=AuditActor.ADMIN,
)
async def clear_location_roi_config(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
):
    """Drop this location's own numbers and go back to the institution's."""
    _require_institution(current_user)
    async with get_db_session() as session:
        location, _ = await _location_for_roi(session, loc_slug, current_user)
        location.roi_config = None
    return None


@router.get(
    "/locations/{loc_slug}/roi/calculate",
    response_model=LocationROICalculationResponse,
    dependencies=[Depends(require_location_scope())],
)
async def calculate_location_roi(
    loc_slug: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    start_date: date_type | None = Query(
        None, description="Inclusive window start (YYYY-MM-DD). Defaults to the 1st."
    ),
    end_date: date_type | None = Query(
        None, description="Inclusive window end (YYYY-MM-DD). Defaults to today."
    ),
):
    from src.app.models.call import Call, CallStatus

    institution_id = _require_institution(current_user)
    period_start, period_end = _roi_window(start_date, end_date)

    async with get_db_session() as session:
        location, institution = await _location_for_roi(
            session, loc_slug, current_user
        )
        resolved = _resolved_location_roi(location, institution)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "ROI configuration not set for this location or its "
                    "institution. Please configure ROI settings first."
                ),
            )
        values, source = resolved
        location_id = str(location.id)

        def _count(*extra):
            return select(func.count(Call.id)).where(
                Call.institution_id == institution_id,
                Call.location_id == location_id,
                Call.call_date >= period_start,
                Call.call_date <= period_end,
                *extra,
            )

        total_calls_month = (await session.execute(_count())).scalar_one()
        appointments_booked_month = (
            await session.execute(
                _count(Call.call_status == CallStatus.APPOINTMENT_BOOKED.value)
            )
        ).scalar_one()
        new_patients_month = (
            await session.execute(_count(Call.is_new_patient.is_(True)))
        ).scalar_one()

        billing_mode = _billing_mode(institution)
        own_cost = _location_subscription_cost(location)

        if billing_mode == "location":
            institution_calls_month = None
        else:
            # Apportion the institution subscription by this location's share of
            # the month's calls. Counting calls with no location at all in the
            # denominator would shrink every location's share and make the group
            # look more profitable than it is, so they are excluded from both
            # sides.
            institution_calls_month = (
                await session.execute(
                    select(func.count(Call.id)).where(
                        Call.institution_id == institution_id,
                        Call.location_id.is_not(None),
                        Call.call_date >= period_start,
                        Call.call_date <= period_end,
                    )
                )
            ).scalar_one()

        institution_cost = float(
            (institution.roi_config or {}).get("monthly_subscription_cost") or 0.0
        )

    if billing_mode == "location":
        # Billed directly, so there is nothing to apportion. An unset price is
        # said out loud rather than treated as free: a clinic reading a net
        # value that silently omitted its own subscription would be reading a
        # number no invoice agrees with.
        monthly_cost_allocated = round(own_cost or 0.0, 2)
        cost_allocation_basis = (
            f"Billed per location: {monthly_cost_allocated:.2f} charged directly "
            "to this clinic"
            if own_cost is not None
            else (
                "Billed per location, but this location has no monthly price "
                "set, so no subscription cost is included"
            )
        )
    elif institution_calls_month:
        share = total_calls_month / institution_calls_month
        monthly_cost_allocated = round(institution_cost * share, 2)
        cost_allocation_basis = (
            f"Billed per institution: {total_calls_month} of "
            f"{institution_calls_month} located calls in this period "
            f"({share:.1%} of the institution subscription)"
        )
    else:
        monthly_cost_allocated = 0.0
        cost_allocation_basis = (
            "Billed per institution, but there were no located calls in this "
            "period, so no subscription cost is apportioned"
        )

    revenue_from_bookings = appointments_booked_month * values["avg_appointment_value"]
    revenue_from_new_patients = new_patients_month * values["avg_new_patient_value"]
    total_revenue_generated = revenue_from_bookings + revenue_from_new_patients

    staff_time_saved_hours = round(
        (total_calls_month * (values["avg_call_duration_minutes"] or 0.0)) / 60, 2
    )
    # No rate configured means the saving is unknown, not nil, so it is left out
    # of the total rather than added as zero. Reporting it as zero would let a
    # clinic conclude the AI saved its front desk nothing.
    rate = values["staff_hourly_rate"]
    staff_cost_saved = None if rate is None else round(staff_time_saved_hours * rate, 2)

    total_value = round(total_revenue_generated + (staff_cost_saved or 0.0), 2)
    net_value = round(total_value - monthly_cost_allocated, 2)
    roi_percentage = (
        round((net_value / monthly_cost_allocated) * 100, 2)
        if monthly_cost_allocated > 0
        else None
    )

    return LocationROICalculationResponse(
        config=LocationROIConfigResponse(
            location_id=location_id,
            location_slug=loc_slug,
            source=source,
            monthly_subscription_cost=own_cost,
            subscription_billing_mode=billing_mode,
            **values,
        ),
        total_calls_month=total_calls_month,
        appointments_booked_month=appointments_booked_month,
        new_patients_month=new_patients_month,
        revenue_from_bookings=round(revenue_from_bookings, 2),
        revenue_from_new_patients=round(revenue_from_new_patients, 2),
        total_revenue_generated=round(total_revenue_generated, 2),
        staff_time_saved_hours=staff_time_saved_hours,
        staff_cost_saved=staff_cost_saved,
        total_value=total_value,
        monthly_cost_allocated=monthly_cost_allocated,
        cost_allocation_basis=cost_allocation_basis,
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
        filter_expr = AuditLog.location_id == location_id
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
    "/locations/{loc_slug}/insurance-plans",
    response_model=list[InsurancePlanResponse],
    dependencies=[Depends(require_location_scope())],
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

        plans = (
            (
                await session.execute(
                    select(InsurancePlan)
                    .where(
                        InsurancePlan.location_id == str(location.id),
                        InsurancePlan.institution_id == current_user.institution_id,
                        InsurancePlan.is_active.is_(True),
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
    dependencies=[Depends(require_location_scope())],
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

        plan = InsurancePlan(
            institution_id=current_user.institution_id,
            location_id=str(location.id),
            name=data.name,
            description=data.description,
        )
        session.add(plan)
        await session.flush()

        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
    dependencies=[Depends(require_location_scope())],
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
    dependencies=[Depends(require_location_scope())],
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
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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
