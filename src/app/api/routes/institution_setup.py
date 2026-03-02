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

from src.app.api.deps import get_current_active_user
from src.app.database import get_db_session
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_operatory import InstitutionOperatory
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.user import User
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
            select(Institution).where(Institution.id == user.institution_id, Institution.is_active == True)
        )
    ).scalar_one_or_none()
    if not institution:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")

    if location_id:
        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == location_id,
                    InstitutionLocation.institution_id == institution.id,
                    InstitutionLocation.is_active == True,
                )
            )
        ).scalar_one_or_none()
    else:
        # Default: first active location
        location = (
            await session.execute(
                select(InstitutionLocation)
                .where(InstitutionLocation.institution_id == institution.id, InstitutionLocation.is_active == True)
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
    source: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    specialty: str | None = None
    is_active: bool = True
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedAppointmentTypeResponse(BaseModel):
    id: str
    source_id: str
    source: str
    name: str
    duration_minutes: int | None = None
    source_metadata: dict | None = None
    is_active: bool = True
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedOperatoryResponse(BaseModel):
    id: str
    source_id: str
    source: str
    name: str
    is_active: bool = True
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class CachedDescriptorResponse(BaseModel):
    id: str
    source_id: str
    source: str
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
    source: str
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
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

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
            pms_source=adapter.source,
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
        result = await session.execute(
            select(InstitutionLocation)
            .where(InstitutionLocation.institution_id == current_user.institution_id, InstitutionLocation.is_active == True)
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
                InstitutionProvider.is_active == True,
            )
            .order_by(InstitutionProvider.name)
        )
        return [CachedProviderResponse.model_validate(p) for p in result.scalars().all()]


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
                InstitutionAppointmentType.is_active == True,
            )
            .order_by(InstitutionAppointmentType.name)
        )
        return [CachedAppointmentTypeResponse.model_validate(at) for at in result.scalars().all()]


@router.post("/appointment-types", response_model=CachedAppointmentTypeResponse, status_code=201)
async def create_appointment_type(
    req: CreateAppointmentTypeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
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


@router.delete("/appointment-types/{source_id}", status_code=204)
async def delete_appointment_type(
    source_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
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
                InstitutionDescriptor.is_active == True,
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
                source="nexhealth",
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
    current_user: Annotated[User, Depends(get_current_active_user)],
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
            source="nexhealth",
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
    current_user: Annotated[User, Depends(get_current_active_user)],
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
