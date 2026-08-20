"""
Institution setup routes — institution-facing API for managing practice configuration.

Reads from cached tables where possible (reduces NexHealth API costs).
Proxies mutations to PMS and refreshes the local cache.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import (
    get_current_active_user,
    get_current_institution_or_location_admin,
)
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_operatory import InstitutionOperatory
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.user import User, UserRole
from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.services.audit import log_audit_background
from src.app.services.sms_privacy import safe_error_summary
from src.app.services.sync_service import SyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/setup", tags=["Institution Setup"])


# ── Helpers ──────────────────────────────────────────────────────────────


async def _resolve_institution_location(
    user: User,
    session: AsyncSession,
    location_id: str | None = None,
) -> tuple[Institution, InstitutionLocation]:
    """Resolve the institution and location for the current user."""
    if not user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    institution = (
        await session.execute(
            select(Institution).where(
                Institution.id == user.institution_id,
                Institution.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not institution:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")

    # Location-scoped users are hard-limited to their own location.
    if user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        if not user.location_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Location-scoped user missing location assignment")
        if location_id and str(location_id) != str(user.location_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot access another location")
        location_id = str(user.location_id)

    if location_id:
        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == location_id,
                    InstitutionLocation.institution_id == institution.id,
                    InstitutionLocation.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    else:
        # No location_id supplied. Auto-defaulting to the oldest active
        # location is only safe for single-location institutions; for
        # multi-location ones the caller must be explicit, otherwise a
        # mutating route would silently target the wrong NexHealth
        # subaccount (cross-clinic write). Look up all active locations
        # and pick the single one if there's exactly one.
        active_locations = list(
            (
                await session.execute(
                    select(InstitutionLocation)
                    .where(
                        InstitutionLocation.institution_id == institution.id,
                        InstitutionLocation.is_active.is_(True),
                    )
                    .order_by(InstitutionLocation.created_at)
                )
            ).scalars()
        )
        if not active_locations:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No active location found for institution"
            )
        if len(active_locations) > 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                (
                    "location_id is required for multi-location institutions. "
                    f"Active locations: {len(active_locations)}. Pass "
                    "?location_id=<id> to disambiguate."
                ),
            )
        location = active_locations[0]

    if not location:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active location found for institution")

    return institution, location


async def _get_adapter(institution: Institution, location: InstitutionLocation) -> PMSAdapter:
    """Get PMS adapter for institution+location."""
    try:
        return await get_adapter_for_institution_location(institution, location)
    except Exception as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PMS not configured: {e}")


# ── Response schemas ─────────────────────────────────────────────────────


class CachedProviderResponse(BaseModel):
    id: str
    source_id: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    specialty: str | None = None
    is_active: bool = True
    buffer_minutes: int = 0
    same_day_cutoff_time: str | None = None
    min_age: int | None = None
    max_age: int | None = None
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_provider(cls, p: Any) -> "CachedProviderResponse":
        return cls(
            id=str(p.id),
            source_id=p.source_id,
            name=p.name,
            first_name=p.first_name,
            last_name=p.last_name,
            specialty=p.specialty,
            is_active=p.is_active,
            buffer_minutes=p.buffer_minutes,
            same_day_cutoff_time=p.same_day_cutoff_time.strftime("%H:%M") if p.same_day_cutoff_time else None,
            min_age=p.min_age,
            max_age=p.max_age,
            synced_at=p.synced_at,
        )


class CachedAppointmentTypeResponse(BaseModel):
    id: str
    source_id: str
    name: str
    duration_minutes: int | None = None
    source_metadata: dict | None = None
    is_active: bool = True
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedOperatoryResponse(BaseModel):
    id: str
    source_id: str
    name: str
    is_active: bool = True
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedDescriptorResponse(BaseModel):
    id: str
    source_id: str
    name: str
    descriptor_type: str | None = None
    code: str | None = None
    is_active: bool = True
    source_metadata: dict | None = None
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedAvailabilityResponse(BaseModel):
    id: str
    source_id: str
    provider_source_id: str | None = None
    provider_name: str | None = None
    operatory_source_id: str | None = None
    operatory_name: str | None = None
    begin_time: str | None = None
    end_time: str | None = None
    days: list[str] | None = None
    specific_date: str | None = None
    appointment_type_ids: list[str] | None = None
    appointment_type_names: list[str] | None = None
    active: bool = True
    synced: bool = False
    source_metadata: dict | None = None
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


def _prefixed_nexhealth_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    value_str = str(value)
    return value_str if value_str.startswith("nh-") else f"nh-{value_str}"


def _availability_response_from_raw(
    item: dict[str, Any],
    *,
    fallback_source_id: str | None = None,
) -> CachedAvailabilityResponse:
    raw_id = item.get("id") or fallback_source_id
    appointment_types = item.get("appointment_types") or []
    raw_type_ids = item.get("appointment_type_ids") or []
    type_ids = [
        _prefixed_nexhealth_id(at.get("id"))
        for at in appointment_types
        if at.get("id") is not None
    ] or [
        _prefixed_nexhealth_id(type_id)
        for type_id in raw_type_ids
        if type_id is not None
    ]

    return CachedAvailabilityResponse(
        id=str(raw_id or ""),
        source_id=_prefixed_nexhealth_id(raw_id) or "",
        provider_source_id=_prefixed_nexhealth_id(item.get("provider_id")),
        provider_name=item.get("provider_name"),
        operatory_source_id=_prefixed_nexhealth_id(item.get("operatory_id")),
        operatory_name=item.get("operatory_name"),
        begin_time=item.get("begin_time"),
        end_time=item.get("end_time"),
        days=item.get("days"),
        specific_date=item.get("specific_date"),
        appointment_type_ids=[type_id for type_id in type_ids if type_id],
        appointment_type_names=[
            at.get("name", "")
            for at in appointment_types
            if at.get("name") is not None
        ],
        active=item.get("active", True),
        synced=item.get("synced", False),
        source_metadata={
            "tz_offset": item.get("tz_offset"),
            "custom_recurrence": item.get("custom_recurrence"),
        },
    )


def _today_for_location(location: InstitutionLocation) -> str:
    timezone_name = location.timezone or "UTC"
    try:
        return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return datetime.utcnow().date().isoformat()


def _time_from_iso(value: str | None) -> str | None:
    if not value:
        return None
    time_part = str(value).split("T", 1)[1] if "T" in str(value) else str(value)
    time_part = time_part.replace("Z", "")
    if "+" in time_part:
        time_part = time_part.split("+", 1)[0]
    elif len(time_part) > 8 and time_part[8] == "-":
        time_part = time_part[:8]
    return time_part[:5] if len(time_part) >= 5 else time_part


def _date_from_iso(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).split("T", 1)[0]


# Bulk range linking is throttled so a wide selection cannot exhaust the
# NexHealth request quota: the client applies at most BULK_LINK_BATCH_SIZE
# windows per request and waits BULK_LINK_BATCH_PAUSE_SECONDS between batches.
# Both values are handed to the client by the preview endpoint so the pacing
# lives in one place.
BULK_LINK_MAX_RANGE_DAYS = 15
BULK_LINK_BATCH_SIZE = 10
BULK_LINK_BATCH_PAUSE_SECONDS = 30


def _strip_source_prefix(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if "-" in text:
        prefix, rest = text.split("-", 1)
        if prefix in {"nh", "gt"}:
            return rest
    return text


def _same_source_id(left: Any, right: Any) -> bool:
    left_id = _strip_source_prefix(left)
    right_id = _strip_source_prefix(right)
    return bool(left_id and right_id and left_id == right_id)


def _parse_range_dates(
    location: InstitutionLocation,
    start_date: str,
    end_date: str,
) -> list[str]:
    """Validate a forward-looking range and expand it to inclusive ISO dates.

    The range must start no earlier than today in the location's timezone and
    span at most BULK_LINK_MAX_RANGE_DAYS days.
    """
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "start_date and end_date must be YYYY-MM-DD dates",
        ) from None

    if end < start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_date must not be before start_date")

    today = date.fromisoformat(_today_for_location(location))
    if start < today:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date must not be in the past")

    day_count = (end - start).days + 1
    if day_count > BULK_LINK_MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Date range must not exceed {BULK_LINK_MAX_RANGE_DAYS} days",
        )

    return [(start + timedelta(days=offset)).isoformat() for offset in range(day_count)]


def _availability_matches_dates(item: dict[str, Any], dates: set[str]) -> bool:
    if item.get("active") is False:
        return False
    specific_date = item.get("specific_date")
    return bool(specific_date and str(specific_date) in dates)


def _match_availabilities_in_range(
    raw_items: list[dict[str, Any]],
    *,
    dates: set[str],
    operatory_id: str | None,
) -> list[dict[str, Any]]:
    """Dated work windows falling inside `dates`, optionally one operatory only.

    Recurring rows (`days` with no `specific_date`) are skipped on purpose:
    patching them would change every future week, not just the selected range.
    """
    return [
        item
        for item in raw_items
        if item.get("id") not in (None, "")
        and _availability_matches_dates(item, dates)
        and (operatory_id is None or _same_source_id(item.get("operatory_id"), operatory_id))
    ]


def _availability_response_from_slot(slot: Any, *, index: int) -> CachedAvailabilityResponse:
    source_id = f"gt-slot-{slot.provider_id or 'provider'}-{slot.operatory_id or 'operatory'}-{slot.start or index}"
    appointment_type_ids = [slot.appointment_type_id] if slot.appointment_type_id else []

    return CachedAvailabilityResponse(
        id=source_id,
        source_id=source_id,
        provider_source_id=slot.provider_id,
        provider_name=slot.provider_name or None,
        operatory_source_id=slot.operatory_id,
        operatory_name=slot.operatory_name,
        begin_time=_time_from_iso(slot.start),
        end_time=_time_from_iso(slot.end),
        days=[],
        specific_date=_date_from_iso(slot.start),
        appointment_type_ids=appointment_type_ids,
        appointment_type_names=[],
        active=True,
        synced=True,
        source_metadata={
            "kind": "bookable_slot",
            "source": "gotracker",
            "location_source_id": slot.location_id,
            "start": slot.start,
            "end": slot.end,
        },
    )


class LocationInfoResponse(BaseModel):
    id: str
    name: str
    slug: str
    timezone: str = "UTC"

    model_config = {"from_attributes": True}


class SetupOverviewResponse(BaseModel):
    location: LocationInfoResponse
    pms_source: str | None = None
    can_create_appointment_types: bool = False
    can_link_availability: bool = False
    counts: dict[str, int] = {}
    # False for call-intelligence-only tenants — the UI hides Practice Setup.
    has_pms: bool = True


# ── Request schemas ──────────────────────────────────────────────────────


class CreateAppointmentTypeRequest(BaseModel):
    name: str
    duration_minutes: int
    descriptor_ids: list[str] = []
    provider_ids: list[str] = []
    operatory_ids: list[str] = []
    bookable_online: bool | None = None


class UpdateAppointmentTypeRequest(BaseModel):
    name: str | None = None
    duration_minutes: int | None = None
    descriptor_ids: list[str] | None = None
    provider_ids: list[str] | None = None
    operatory_ids: list[str] | None = None
    bookable_online: bool | None = None


class CreateAvailabilityRequest(BaseModel):
    provider_id: str
    appointment_type_ids: list[str]
    operatory_id: str
    days: list[str]
    start_time: str
    end_time: str


class UpdateAvailabilityRequest(BaseModel):
    appointment_type_ids: list[str] | None = None
    days: list[str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    operatory_id: str | None = None
    active: bool | None = None


class BulkLinkRangePreviewRequest(BaseModel):
    provider_id: str
    start_date: str
    end_date: str
    operatory_id: str | None = None


class BulkLinkRangePreviewResponse(BaseModel):
    start_date: str
    end_date: str
    day_count: int
    matched_count: int
    windows: list[CachedAvailabilityResponse]
    batch_size: int
    batch_pause_seconds: int


class BulkLinkRangeApplyRequest(BaseModel):
    availability_ids: list[str] = Field(min_length=1, max_length=BULK_LINK_BATCH_SIZE)
    appointment_type_ids: list[str] = Field(min_length=1)


class BulkLinkRangeApplyResponse(BaseModel):
    updated_count: int
    updated_ids: list[str] = []
    errors: list[str] = []


# ── Overview ─────────────────────────────────────────────────────────────


@router.get("/overview", response_model=SetupOverviewResponse)
async def get_setup_overview(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """Get setup overview: location info, PMS capabilities, and cached data counts."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)

        # Call-intelligence-only tenant: no PMS adapter, nothing to sync.
        if not institution.has_pms:
            return SetupOverviewResponse(
                location=LocationInfoResponse.model_validate(location),
                pms_source=None,
                can_create_appointment_types=False,
                can_link_availability=False,
                counts={"providers": 0, "appointment_types": 0, "operatories": 0, "descriptors": 0},
                has_pms=False,
            )

        adapter = await _get_adapter(institution, location)

        # Counts from cache
        counts: dict[str, int] = {}
        for label, model in [
            ("providers", InstitutionProvider),
            ("appointment_types", InstitutionAppointmentType),
            ("operatories", InstitutionOperatory),
            ("descriptors", InstitutionDescriptor),
        ]:
            q = select(model).where(
                model.institution_id == institution.id, model.location_id == location.id
            )
            result = await session.execute(q)
            counts[label] = len(result.scalars().all())

        return SetupOverviewResponse(
            location=LocationInfoResponse.model_validate(location),
            pms_source=adapter.source,
            can_create_appointment_types=isinstance(adapter, SupportsAppointmentTypeCreation),
            can_link_availability=isinstance(adapter, SupportsAvailabilityLinking),
            counts=counts,
            has_pms=True,
        )


# ── Locations (for institution with multiple) ─────────────────────────────


@router.get("/locations", response_model=list[LocationInfoResponse])
async def list_institution_locations(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """List active locations for the institution."""
    if not current_user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    async with get_db_session() as session:
        if current_user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
            if not current_user.location_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Location-scoped user missing location assignment")
            result = await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == current_user.location_id,
                    InstitutionLocation.institution_id == current_user.institution_id,
                    InstitutionLocation.is_active.is_(True),
                )
            )
        else:
            result = await session.execute(
                select(InstitutionLocation)
                .where(
                    InstitutionLocation.institution_id == current_user.institution_id,
                    InstitutionLocation.is_active.is_(True),
                )
                .order_by(InstitutionLocation.name)
            )
        return [LocationInfoResponse.model_validate(loc) for loc in result.scalars().all()]


# ── Providers (cached) ───────────────────────────────────────────────────


@router.get("/providers", response_model=list[CachedProviderResponse])
async def list_providers(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """List cached providers for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(InstitutionProvider)
            .where(
                InstitutionProvider.institution_id == institution.id,
                InstitutionProvider.location_id == location.id,
                InstitutionProvider.is_active.is_(True),
            )
            .order_by(InstitutionProvider.name)
        )
        return [CachedProviderResponse.from_provider(p) for p in result.scalars().all()]


class UpdateProviderRequest(BaseModel):
    buffer_minutes: int | None = None
    same_day_cutoff_time: str | None = None  # "HH:MM" or null to clear
    min_age: int | None = None  # minimum patient age (inclusive), null to clear
    max_age: int | None = None  # maximum patient age (inclusive), null to clear


@router.patch("/providers/{provider_id}", response_model=CachedProviderResponse)
async def update_provider(
    provider_id: str,
    req: UpdateProviderRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Update provider settings (buffer_minutes, same_day_cutoff_time)."""
    from datetime import datetime

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(InstitutionProvider).where(
                InstitutionProvider.id == provider_id,
                InstitutionProvider.institution_id == institution.id,
                InstitutionProvider.location_id == location.id,
            )
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")

        if "buffer_minutes" in req.model_fields_set and req.buffer_minutes is not None:
            if req.buffer_minutes < 0 or req.buffer_minutes > 1440:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "buffer_minutes must be 0–1440")
            provider.buffer_minutes = req.buffer_minutes

        if "same_day_cutoff_time" in req.model_fields_set:
            if req.same_day_cutoff_time in (None, ""):
                provider.same_day_cutoff_time = None
            else:
                try:
                    # Strict HH:MM format only.
                    provider.same_day_cutoff_time = datetime.strptime(
                        req.same_day_cutoff_time, "%H:%M"
                    ).time()
                except ValueError:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "same_day_cutoff_time must be HH:MM format")

        # ── Age-group fields ─────────────────────────────────────────
        if "min_age" in req.model_fields_set:
            if req.min_age is not None and (req.min_age < 0 or req.min_age > 150):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "min_age must be 0–150")
            provider.min_age = req.min_age

        if "max_age" in req.model_fields_set:
            if req.max_age is not None and (req.max_age < 0 or req.max_age > 150):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "max_age must be 0–150")
            provider.max_age = req.max_age

        # Cross-validate: min must be <= max when both are set
        effective_min = provider.min_age
        effective_max = provider.max_age
        if effective_min is not None and effective_max is not None and effective_min > effective_max:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "min_age cannot be greater than max_age")

        await session.flush()
        await session.refresh(provider)
        response = CachedProviderResponse.from_provider(provider)
        loc_slug = location.slug
        institution_id = institution.id

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/provider:{provider_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "update_provider",
            "fields_changed": sorted(req.model_fields_set),
        },
        institution_id=institution_id,
    )
    return response


# ── Appointment Types (cached + CRUD via PMS) ────────────────────────────


@router.get("/appointment-types", response_model=list[CachedAppointmentTypeResponse])
async def list_appointment_types(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """List cached appointment types for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(InstitutionAppointmentType)
            .where(
                InstitutionAppointmentType.institution_id == institution.id,
                InstitutionAppointmentType.location_id == location.id,
                InstitutionAppointmentType.is_active.is_(True),
            )
            .order_by(InstitutionAppointmentType.name)
        )
        return [CachedAppointmentTypeResponse.model_validate(at) for at in result.scalars().all()]


@router.post("/appointment-types", response_model=CachedAppointmentTypeResponse, status_code=201)
async def create_appointment_type(
    req: CreateAppointmentTypeRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Create appointment type via PMS and cache locally."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAppointmentTypeCreation):
            raise HTTPException(400, "This PMS does not support creating appointment types")
        if adapter.source == "gotracker" and not req.provider_ids:
            raise HTTPException(400, "GoTracker appointment types require at least one provider")

        result = await adapter.create_appointment_type(
            name=req.name,
            duration_minutes=req.duration_minutes,
            descriptor_ids=req.descriptor_ids,
            provider_ids=req.provider_ids,
            operatory_ids=req.operatory_ids,
            bookable_online=req.bookable_online,
        )

        # Cache the newly created appointment type
        sync_svc = SyncService(session)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        await sync_svc._upsert_appointment_type(
            institution_id=institution.id,
            location_id=location.id,
            source=result.source,
            source_id=result.id,
            name=result.name,
            duration_minutes=result.duration_minutes,
            source_metadata=result.source_metadata,
            synced_at=now,
        )
        await session.flush()

        # Return the cached row
        stmt = select(InstitutionAppointmentType).where(
            InstitutionAppointmentType.institution_id == institution.id,
            InstitutionAppointmentType.location_id == location.id,
            InstitutionAppointmentType.source_id == result.id,
        )
        cached = (await session.execute(stmt)).scalar_one_or_none()
        if not cached:
            raise HTTPException(500, "Failed to cache appointment type")

        response = CachedAppointmentTypeResponse.model_validate(cached)
        loc_slug = location.slug
        institution_id = institution.id
        created_source_id = result.id
        created_name = result.name
        created_duration = result.duration_minutes

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/appointment_type:{created_source_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "create_appointment_type",
            "name": created_name,
            "duration_minutes": created_duration,
        },
        institution_id=institution_id,
    )
    return response


@router.patch("/appointment-types/{source_id}", response_model=CachedAppointmentTypeResponse)
async def update_appointment_type(
    source_id: str,
    req: UpdateAppointmentTypeRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Update appointment type via PMS and refresh the local cache."""
    if (
        req.name is None
        and req.duration_minutes is None
        and req.descriptor_ids is None
        and req.provider_ids is None
        and req.operatory_ids is None
        and req.bookable_online is None
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields provided to update")

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAppointmentTypeCreation):
            raise HTTPException(400, "This PMS does not support updating appointment types")
        if adapter.source == "gotracker" and req.provider_ids == []:
            raise HTTPException(400, "GoTracker appointment types require at least one provider")

        result = await adapter.update_appointment_type(
            appointment_type_id=source_id,
            name=req.name,
            duration_minutes=req.duration_minutes,
            descriptor_ids=req.descriptor_ids,
            provider_ids=req.provider_ids,
            operatory_ids=req.operatory_ids,
            bookable_online=req.bookable_online,
        )

        # Update cached row with latest values
        sync_svc = SyncService(session)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        await sync_svc._upsert_appointment_type(
            institution_id=institution.id,
            location_id=location.id,
            source=result.source,
            source_id=result.id,
            name=result.name,
            duration_minutes=result.duration_minutes,
            source_metadata=result.source_metadata,
            synced_at=now,
        )
        await session.flush()

        stmt = select(InstitutionAppointmentType).where(
            InstitutionAppointmentType.institution_id == institution.id,
            InstitutionAppointmentType.location_id == location.id,
            InstitutionAppointmentType.source_id == result.id,
        )
        cached = (await session.execute(stmt)).scalar_one_or_none()
        if not cached:
            raise HTTPException(500, "Failed to cache appointment type")

        response = CachedAppointmentTypeResponse.model_validate(cached)
        loc_slug = location.slug
        institution_id = institution.id
        updated_source_id = result.id
        fields_changed = sorted(req.model_fields_set)

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/appointment_type:{updated_source_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "update_appointment_type",
            "fields_changed": fields_changed,
        },
        institution_id=institution_id,
    )
    return response


@router.delete("/appointment-types/{source_id}", status_code=204)
async def delete_appointment_type(
    source_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Delete appointment type via PMS and remove from cache."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAppointmentTypeCreation):
            raise HTTPException(400, "This PMS does not support deleting appointment types")

        await adapter.delete_appointment_type(source_id)

        # Remove from cache
        stmt = select(InstitutionAppointmentType).where(
            InstitutionAppointmentType.institution_id == institution.id,
            InstitutionAppointmentType.location_id == location.id,
            InstitutionAppointmentType.source_id == source_id,
        )
        cached = (await session.execute(stmt)).scalar_one_or_none()
        if cached:
            await session.delete(cached)
        loc_slug = location.slug
        institution_id = institution.id
        cache_existed = cached is not None

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/appointment_type:{source_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "delete_appointment_type",
            "cache_existed": cache_existed,
        },
        institution_id=institution_id,
    )


# ── Operatories (cached, read-only) ─────────────────────────────────────


@router.get("/operatories", response_model=list[CachedOperatoryResponse])
async def list_operatories(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """List cached operatories for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(InstitutionOperatory)
            .where(
                InstitutionOperatory.institution_id == institution.id,
                InstitutionOperatory.location_id == location.id,
            )
            .order_by(InstitutionOperatory.name)
        )
        return [CachedOperatoryResponse.model_validate(op) for op in result.scalars().all()]


# ── Descriptors (cached, read-only) ─────────────────────────────────────


@router.get("/descriptors", response_model=list[CachedDescriptorResponse])
async def list_descriptors(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """List cached EMR descriptors for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(InstitutionDescriptor)
            .where(
                InstitutionDescriptor.institution_id == institution.id,
                InstitutionDescriptor.location_id == location.id,
                InstitutionDescriptor.is_active.is_(True),
            )
            .order_by(InstitutionDescriptor.name)
        )
        return [CachedDescriptorResponse.model_validate(d) for d in result.scalars().all()]


# ── Availabilities (fetched LIVE from PMS — too volatile for cache) ───────


@router.get("/availabilities", response_model=list[CachedAvailabilityResponse])
async def list_availabilities(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
    provider_source_id: str | None = Query(None, description="Filter by provider"),
    start_date: str | None = Query(
        None,
        description="YYYY-MM-DD start date for slot-derived availability on PMSs without work windows.",
    ),
    days: int = Query(7, ge=1, le=31),
):
    """Fetch schedule availability live from PMS for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        # Build extra params for the PMS call
        extra: dict[str, Any] = {}
        if provider_source_id:
            extra["provider_id"] = provider_source_id

        try:
            if isinstance(adapter, SupportsAvailabilityLinking):
                raw_items = await adapter.list_availabilities(**extra)
                return [_availability_response_from_raw(item) for item in raw_items]

            slot_result = await adapter.find_available_slots(
                start_date=start_date or _today_for_location(location),
                days=days,
                provider_id=provider_source_id,
            )
            return [
                _availability_response_from_slot(slot, index=index)
                for index, slot in enumerate(slot_result.slots)
            ]
        except Exception as e:
            logger.error(
                "Failed to fetch schedule availability from PMS: %s",
                safe_error_summary(e),
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Failed to fetch availabilities",
            )


@router.post(
    "/availabilities/bulk-link-range/preview",
    response_model=BulkLinkRangePreviewResponse,
)
async def preview_bulk_link_range_availabilities(
    req: BulkLinkRangePreviewRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """List the real PMS work windows a range-link would touch, without writing.

    Reading the PMS once here lets the client split the writes into throttled
    batches without re-listing (and re-spending quota) for every batch.
    """
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAvailabilityLinking):
            raise HTTPException(400, "This PMS does not support availability updates")

        range_dates = _parse_range_dates(location, req.start_date, req.end_date)
        raw_items = await adapter.list_availabilities(
            provider_id=req.provider_id,
            ignore_past_dates=False,
        )
        matched_items = _match_availabilities_in_range(
            raw_items,
            dates=set(range_dates),
            operatory_id=req.operatory_id,
        )

    return BulkLinkRangePreviewResponse(
        start_date=range_dates[0],
        end_date=range_dates[-1],
        day_count=len(range_dates),
        matched_count=len(matched_items),
        windows=[_availability_response_from_raw(item) for item in matched_items],
        batch_size=BULK_LINK_BATCH_SIZE,
        batch_pause_seconds=BULK_LINK_BATCH_PAUSE_SECONDS,
    )


@router.post(
    "/availabilities/bulk-link-range/apply",
    response_model=BulkLinkRangeApplyResponse,
)
async def apply_bulk_link_range_availabilities(
    req: BulkLinkRangeApplyRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Link appointment types to one throttled batch of PMS work windows.

    Capped at BULK_LINK_BATCH_SIZE ids per call; the client waits
    BULK_LINK_BATCH_PAUSE_SECONDS between calls so a wide date range does not
    burn through the NexHealth request quota in one burst.
    """
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAvailabilityLinking):
            raise HTTPException(400, "This PMS does not support availability updates")

        async def _link_one(availability_id: str) -> str | Exception:
            try:
                await adapter.update_availability(
                    availability_id=availability_id,
                    appointment_type_ids=req.appointment_type_ids,
                )
                return availability_id
            except Exception as exc:  # surfaced per-window; the batch continues
                return exc

        outcomes = await asyncio.gather(
            *(_link_one(availability_id) for availability_id in req.availability_ids)
        )

        loc_slug = location.slug
        institution_id = institution.id

    updated_ids = [
        availability_id
        for availability_id, outcome in zip(req.availability_ids, outcomes)
        if not isinstance(outcome, Exception)
    ]
    errors = [
        f"{availability_id}: {safe_error_summary(outcome)}"
        for availability_id, outcome in zip(req.availability_ids, outcomes)
        if isinstance(outcome, Exception)
    ]

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/availabilities:range_batch",
        outcome=AuditOutcome.SUCCESS if not errors else AuditOutcome.FAILURE_EXTERNAL_API,
        metadata={
            "actor_role": current_user.role,
            "action": "apply_bulk_link_range_availabilities",
            "appointment_type_ids": req.appointment_type_ids,
            "availability_ids": req.availability_ids,
            "requested_count": len(req.availability_ids),
            "updated_count": len(updated_ids),
            "failed_count": len(errors),
        },
        institution_id=institution_id,
    )
    return BulkLinkRangeApplyResponse(
        updated_count=len(updated_ids),
        updated_ids=updated_ids,
        errors=errors,
    )


@router.post("/availabilities", response_model=CachedAvailabilityResponse, status_code=201)
async def create_availability(
    req: CreateAvailabilityRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Create a PMS work window and return it in the setup response shape."""
    if not req.appointment_type_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "appointment_type_ids is required")
    if not req.days:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days is required")

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAvailabilityLinking):
            raise HTTPException(400, "This PMS does not support creating availability windows")

        raw = await adapter.link_availability(
            provider_id=req.provider_id,
            appointment_type_ids=req.appointment_type_ids,
            operatory_id=req.operatory_id,
            days=req.days,
            start_time=req.start_time,
            end_time=req.end_time,
        )
        created = raw.get("data", raw) if isinstance(raw, dict) else {}
        if not isinstance(created, dict):
            created = {}
        if isinstance(created.get("availability"), dict):
            created = created["availability"]

        response = _availability_response_from_raw(created)
        loc_slug = location.slug
        institution_id = institution.id
        created_source_id = response.source_id

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/availability:{created_source_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "create_availability",
            "provider_id": req.provider_id,
            "operatory_id": req.operatory_id,
            "days": req.days,
        },
        institution_id=institution_id,
    )
    return response


@router.patch("/availabilities/{source_id}", response_model=CachedAvailabilityResponse)
async def update_availability(
    source_id: str,
    req: UpdateAvailabilityRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Update availability via PMS (e.g. link appointment types)."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAvailabilityLinking):
            raise HTTPException(400, "This PMS does not support availability updates")

        updated = await adapter.update_availability(
            availability_id=source_id,
            appointment_type_ids=req.appointment_type_ids,
            days=req.days,
            start_time=req.start_time,
            end_time=req.end_time,
            operatory_id=req.operatory_id,
            active=req.active,
        )

        response = _availability_response_from_raw(updated, fallback_source_id=source_id)
        loc_slug = location.slug
        institution_id = institution.id
        fields_changed = sorted(req.model_fields_set)

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/availability:{source_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "update_availability",
            "fields_changed": fields_changed,
        },
        institution_id=institution_id,
    )
    return response


# ── Sync (trigger fresh sync from PMS) ───────────────────────────────────


@router.post("/sync")
async def trigger_sync(
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Trigger a fresh sync from PMS for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        if not institution.has_pms:
            raise HTTPException(
                status_code=400,
                detail="This institution does not use a PMS; there is nothing to sync.",
            )
        sync_svc = SyncService(session)
        result = await sync_svc.sync_location(institution, location)
        loc_slug = location.slug
        institution_id = institution.id

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_SYNC,
        target_resource=f"location:{loc_slug}/sync",
        outcome=AuditOutcome.SUCCESS if result.success else AuditOutcome.FAILURE_INTERNAL,
        metadata={
            "actor_role": current_user.role,
            "providers_synced": result.providers_synced,
            "appointment_types_synced": result.appointment_types_synced,
            "operatories_synced": result.operatories_synced,
            "descriptors_synced": result.descriptors_synced,
            "errors": result.errors,
        },
        institution_id=institution_id,
    )
    return {
        "success": result.success,
        "location": result.location_slug,
        "providers_synced": result.providers_synced,
        "appointment_types_synced": result.appointment_types_synced,
        "operatories_synced": result.operatories_synced,
        "descriptors_synced": result.descriptors_synced,

        "errors": result.errors,
    }


# ── Operating Hours (institution-facing) ─────────────────────────────────


class OperatingHoursEntry(BaseModel):
    day_of_week: int
    is_open: bool = True
    open_time: str | None = None
    close_time: str | None = None


class OperatingHoursResponse(BaseModel):
    id: str
    location_id: str
    day_of_week: int
    is_open: bool
    open_time: str | None = None
    close_time: str | None = None

    model_config = {"from_attributes": True}

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
    hours: list[OperatingHoursEntry]


class BreakCreateRequest(BaseModel):
    name: str
    day_of_week: int | None = None
    start_time: str
    end_time: str


class BreakResponse(BaseModel):
    id: str
    location_id: str
    name: str
    day_of_week: int | None = None
    start_time: str
    end_time: str

    model_config = {"from_attributes": True}

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


@router.get("/operating-hours", response_model=list[OperatingHoursResponse])
async def get_operating_hours(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """View operating hours for the institution location."""
    from src.app.models.location_operating_hours import LocationOperatingHours

    async with get_db_session() as session:
        _, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(LocationOperatingHours)
            .where(LocationOperatingHours.location_id == location.id)
            .order_by(LocationOperatingHours.day_of_week)
        )
        return [OperatingHoursResponse.from_model(h) for h in result.scalars().all()]


@router.put("/operating-hours", response_model=list[OperatingHoursResponse])
async def set_operating_hours(
    data: BulkOperatingHoursRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Bulk-set operating hours (replaces existing) for the institution location."""
    from datetime import time as dt_time
    from sqlalchemy import delete as sa_delete
    from src.app.models.location_operating_hours import LocationOperatingHours

    async with get_db_session() as session:
        _, location = await _resolve_institution_location(current_user, session, location_id)

        # Validate no duplicate days
        days_seen: set[int] = set()
        for entry in data.hours:
            if entry.day_of_week in days_seen:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Duplicate day_of_week: {entry.day_of_week}")
            days_seen.add(entry.day_of_week)

        # Replace all
        await session.execute(
            sa_delete(LocationOperatingHours).where(LocationOperatingHours.location_id == location.id)
        )

        new_rows = []
        for entry in data.hours:
            open_t = dt_time.fromisoformat(entry.open_time) if entry.open_time else None
            close_t = dt_time.fromisoformat(entry.close_time) if entry.close_time else None
            row = LocationOperatingHours(
                location_id=location.id,
                day_of_week=entry.day_of_week,
                is_open=entry.is_open,
                open_time=open_t,
                close_time=close_t,
            )
            session.add(row)
            new_rows.append(row)

        await session.flush()
        response = [OperatingHoursResponse.from_model(r) for r in new_rows]
        loc_slug = location.slug

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/operating_hours",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "set_operating_hours",
            "entries_count": len(data.hours),
        },
        institution_id=current_user.institution_id,
    )
    return response


# ── Breaks (institution-facing) ──────────────────────────────────────────


@router.get("/breaks", response_model=list[BreakResponse])
async def get_breaks(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """View breaks for the institution location."""
    from src.app.models.location_break import LocationBreak

    async with get_db_session() as session:
        _, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(LocationBreak)
            .where(LocationBreak.location_id == location.id)
            .order_by(LocationBreak.day_of_week.nulls_first(), LocationBreak.start_time)
        )
        return [BreakResponse.from_model(b) for b in result.scalars().all()]


@router.post("/breaks", response_model=BreakResponse, status_code=201)
async def create_break(
    data: BreakCreateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Add a break for the institution location."""
    from datetime import time as dt_time
    from src.app.models.location_break import LocationBreak

    async with get_db_session() as session:
        _, location = await _resolve_institution_location(current_user, session, location_id)
        brk = LocationBreak(
            location_id=location.id,
            name=data.name,
            day_of_week=data.day_of_week,
            start_time=dt_time.fromisoformat(data.start_time),
            end_time=dt_time.fromisoformat(data.end_time),
        )
        session.add(brk)
        await session.flush()
        response = BreakResponse.from_model(brk)
        loc_slug = location.slug
        new_break_id = str(brk.id)

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/break:{new_break_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "create_break",
            "name": data.name,
            "day_of_week": data.day_of_week,
            "start_time": data.start_time,
            "end_time": data.end_time,
        },
        institution_id=current_user.institution_id,
    )
    return response


@router.delete("/breaks/{break_id}", status_code=204)
async def delete_break(
    break_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Remove a break from the institution location."""
    from src.app.models.location_break import LocationBreak

    async with get_db_session() as session:
        _, location = await _resolve_institution_location(current_user, session, location_id)
        result = await session.execute(
            select(LocationBreak).where(
                LocationBreak.id == break_id,
                LocationBreak.location_id == location.id,
            )
        )
        brk = result.scalar_one_or_none()
        if not brk:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Break not found")
        await session.delete(brk)
        loc_slug = location.slug

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.LOCATION_UPDATE,
        target_resource=f"location:{loc_slug}/break:{break_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "action": "delete_break",
        },
        institution_id=current_user.institution_id,
    )
