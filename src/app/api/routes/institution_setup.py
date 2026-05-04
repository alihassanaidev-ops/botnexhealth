"""
Institution setup routes — institution-facing API for managing practice configuration.

Reads from cached tables where possible (reduces NexHealth API costs).
Proxies mutations to PMS and refreshes the local cache.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import (
    get_current_active_user,
    get_current_institution_or_location_admin,
)
from src.app.database import get_db_session
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_operatory import InstitutionOperatory
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.user import User, UserRole
from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking
from src.app.pms.factory import get_adapter_for_institution_location
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
        # Default: first active location
        location = (
            await session.execute(
                select(InstitutionLocation)
                .where(
                    InstitutionLocation.institution_id == institution.id,
                    InstitutionLocation.is_active.is_(True),
                )
                .order_by(InstitutionLocation.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

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


class LocationInfoResponse(BaseModel):
    id: str
    name: str
    slug: str

    model_config = {"from_attributes": True}


class SetupOverviewResponse(BaseModel):
    location: LocationInfoResponse
    pms_source: str | None = None
    can_create_appointment_types: bool = False
    can_link_availability: bool = False
    counts: dict[str, int] = {}


# ── Request schemas ──────────────────────────────────────────────────────


class CreateAppointmentTypeRequest(BaseModel):
    name: str
    duration_minutes: int
    descriptor_ids: list[str] = []


class UpdateAppointmentTypeRequest(BaseModel):
    name: str | None = None
    duration_minutes: int | None = None
    descriptor_ids: list[str] | None = None


class UpdateAvailabilityRequest(BaseModel):
    appointment_type_ids: list[str] | None = None
    days: list[str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    operatory_id: str | None = None
    active: bool | None = None


# ── Overview ─────────────────────────────────────────────────────────────


@router.get("/overview", response_model=SetupOverviewResponse)
async def get_setup_overview(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = Query(None),
):
    """Get setup overview: location info, PMS capabilities, and cached data counts."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
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
            pms_source=None,
            can_create_appointment_types=isinstance(adapter, SupportsAppointmentTypeCreation),
            can_link_availability=isinstance(adapter, SupportsAvailabilityLinking),
            counts=counts,
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
        return CachedProviderResponse.from_provider(provider)


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

        result = await adapter.create_appointment_type(
            name=req.name,
            duration_minutes=req.duration_minutes,
            descriptor_ids=req.descriptor_ids,
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

        return CachedAppointmentTypeResponse.model_validate(cached)


@router.patch("/appointment-types/{source_id}", response_model=CachedAppointmentTypeResponse)
async def update_appointment_type(
    source_id: str,
    req: UpdateAppointmentTypeRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Update appointment type via PMS and refresh the local cache."""
    if req.name is None and req.duration_minutes is None and req.descriptor_ids is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields provided to update")

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        if not isinstance(adapter, SupportsAppointmentTypeCreation):
            raise HTTPException(400, "This PMS does not support updating appointment types")

        result = await adapter.update_appointment_type(
            appointment_type_id=source_id,
            name=req.name,
            duration_minutes=req.duration_minutes,
            descriptor_ids=req.descriptor_ids,
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

        return CachedAppointmentTypeResponse.model_validate(cached)


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

        # Strip prefix if present (e.g., "nh-123" -> "123")
        raw_id = source_id.removeprefix("nh-")

        from src.app.api.helpers import handle_nexhealth_request
        if hasattr(adapter, "_client"):
            params = {"subdomain": adapter._subdomain} if adapter._subdomain else {}
            await handle_nexhealth_request(
                adapter._client, "DELETE", f"/appointment_types/{raw_id}", params=params
            )

        # Remove from cache
        stmt = select(InstitutionAppointmentType).where(
            InstitutionAppointmentType.institution_id == institution.id,
            InstitutionAppointmentType.location_id == location.id,
            InstitutionAppointmentType.source_id == source_id,
        )
        cached = (await session.execute(stmt)).scalar_one_or_none()
        if cached:
            await session.delete(cached)


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
):
    """Fetch availabilities live from PMS for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        adapter = await _get_adapter(institution, location)

        # Build extra params for the PMS call
        extra: dict[str, Any] = {}
        if provider_source_id:
            # Strip prefix (e.g. "nh-449151038" -> "449151038")
            raw_pid = provider_source_id.removeprefix("nh-")
            extra["provider_id"] = raw_pid

        try:
            raw_items = await adapter.list_availabilities(**extra)
        except Exception as e:
            logger.error(f"Failed to fetch availabilities from PMS: {e}")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to fetch availabilities: {e}")

        # Map raw PMS response to the response schema
        results: list[CachedAvailabilityResponse] = []
        for item in raw_items:
            appt_types = item.get("appointment_types") or []
            results.append(CachedAvailabilityResponse(
                id=str(item.get("id", "")),
                source_id=f"nh-{item['id']}" if item.get("id") else "",
                provider_source_id=f"nh-{item['provider_id']}" if item.get("provider_id") else None,
                provider_name=item.get("provider_name"),
                operatory_source_id=f"nh-{item['operatory_id']}" if item.get("operatory_id") else None,
                operatory_name=item.get("operatory_name"),
                begin_time=item.get("begin_time"),
                end_time=item.get("end_time"),
                days=item.get("days"),
                specific_date=item.get("specific_date"),
                appointment_type_ids=[f"nh-{at.get('id')}" for at in appt_types],
                appointment_type_names=[at.get("name", "") for at in appt_types],
                active=item.get("active", True),
                synced=item.get("synced", False),
                source_metadata={
                    "tz_offset": item.get("tz_offset"),
                    "custom_recurrence": item.get("custom_recurrence"),
                },
            ))
        return results


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

        # Return the PMS response directly
        appt_types = updated.get("appointment_types") or []
        return CachedAvailabilityResponse(
            id=str(updated.get("id", source_id)),
            source_id=f"nh-{updated.get('id', source_id)}",
            provider_source_id=f"nh-{updated['provider_id']}" if updated.get("provider_id") else None,
            provider_name=updated.get("provider_name"),
            operatory_source_id=f"nh-{updated['operatory_id']}" if updated.get("operatory_id") else None,
            operatory_name=updated.get("operatory_name"),
            begin_time=updated.get("begin_time"),
            end_time=updated.get("end_time"),
            days=updated.get("days"),
            specific_date=updated.get("specific_date"),
            appointment_type_ids=[f"nh-{at.get('id')}" for at in appt_types],
            appointment_type_names=[at.get("name", "") for at in appt_types],
            active=updated.get("active", True),
            synced=updated.get("synced", False),
            source_metadata={
                "tz_offset": updated.get("tz_offset"),
                "custom_recurrence": updated.get("custom_recurrence"),
            },
        )


# ── Sync (trigger fresh sync from PMS) ───────────────────────────────────


@router.post("/sync")
async def trigger_sync(
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
    location_id: str | None = Query(None),
):
    """Trigger a fresh sync from PMS for the institution location."""
    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(current_user, session, location_id)
        sync_svc = SyncService(session)
        result = await sync_svc.sync_location(institution, location)
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
        return [OperatingHoursResponse.from_model(r) for r in new_rows]


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
        return BreakResponse.from_model(brk)


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
