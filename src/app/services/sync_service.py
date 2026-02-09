"""Sync service — fetches providers & appointment types from PMS and caches locally."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.tenant_appointment_type import TenantAppointmentType
from src.app.models.tenant_provider import TenantProvider
from src.app.services.audit import log_audit_background

if TYPE_CHECKING:
    from src.app.models.tenant import Tenant
    from src.app.models.tenant_location import TenantLocation

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation for a single location."""

    location_slug: str
    providers_synced: int = 0
    appointment_types_synced: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class SyncService:
    """Syncs PMS provider/appointment-type data into local cache tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def sync_location(self, tenant: Tenant, location: TenantLocation) -> SyncResult:
        """Sync providers and appointment types for a single location."""
        from src.app.pms.factory import get_adapter_for_tenant_location

        result = SyncResult(location_slug=location.slug)
        now = datetime.now(timezone.utc)

        try:
            adapter = await get_adapter_for_tenant_location(tenant, location)
        except Exception as e:
            result.errors.append(f"Failed to get PMS adapter: {e}")
            self._audit_sync(tenant, location, result)
            return result

        # Sync providers
        try:
            pms_providers = await adapter.list_providers()
            for p in pms_providers:
                await self._upsert_provider(
                    tenant_id=tenant.id,
                    location_id=location.id,
                    source=adapter.source,
                    source_id=p.id,
                    name=p.name,
                    first_name=p.first_name,
                    last_name=p.last_name,
                    specialty=p.specialty,
                    synced_at=now,
                )
                result.providers_synced += 1
        except Exception as e:
            logger.error(f"Provider sync failed for {location.slug}: {e}")
            result.errors.append(f"Provider sync error: {e}")

        # Sync appointment types
        try:
            pms_types = await adapter.list_appointment_types()
            for at in pms_types:
                await self._upsert_appointment_type(
                    tenant_id=tenant.id,
                    location_id=location.id,
                    source=adapter.source,
                    source_id=at.id,
                    name=at.name,
                    duration_minutes=at.duration_minutes,
                    source_metadata=at.source_metadata,
                    synced_at=now,
                )
                result.appointment_types_synced += 1
        except Exception as e:
            logger.error(f"Appointment type sync failed for {location.slug}: {e}")
            result.errors.append(f"Appointment type sync error: {e}")

        await self.session.flush()
        self._audit_sync(tenant, location, result)
        logger.info(
            f"Sync complete for {location.slug}: "
            f"{result.providers_synced} providers, "
            f"{result.appointment_types_synced} appointment types"
        )
        return result

    async def sync_all_locations(self, tenant: Tenant, locations: list[TenantLocation]) -> dict[str, SyncResult]:
        """Sync all active locations for a tenant."""
        results: dict[str, SyncResult] = {}
        for loc in locations:
            if loc.is_active:
                results[loc.slug] = await self.sync_location(tenant, loc)
        return results

    # ── Upsert helpers ──────────────────────────────────────────────────

    async def _upsert_provider(
        self,
        tenant_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str | None,
        first_name: str | None,
        last_name: str | None,
        specialty: str | None,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached provider row by (tenant_id, location_id, source_id)."""
        stmt = select(TenantProvider).where(
            TenantProvider.tenant_id == tenant_id,
            TenantProvider.location_id == location_id,
            TenantProvider.source_id == source_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.name = name
            existing.first_name = first_name
            existing.last_name = last_name
            existing.specialty = specialty
            existing.source = source
            existing.is_active = True
            existing.synced_at = synced_at
        else:
            self.session.add(
                TenantProvider(
                    tenant_id=tenant_id,
                    location_id=location_id,
                    source=source,
                    source_id=source_id,
                    name=name,
                    first_name=first_name,
                    last_name=last_name,
                    specialty=specialty,
                    synced_at=synced_at,
                )
            )

    async def _upsert_appointment_type(
        self,
        tenant_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str,
        duration_minutes: int | None,
        source_metadata: dict | None,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached appointment type row by (tenant_id, location_id, source_id)."""
        stmt = select(TenantAppointmentType).where(
            TenantAppointmentType.tenant_id == tenant_id,
            TenantAppointmentType.location_id == location_id,
            TenantAppointmentType.source_id == source_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.name = name
            existing.duration_minutes = duration_minutes
            existing.source_metadata = source_metadata
            existing.source = source
            existing.is_active = True
            existing.synced_at = synced_at
        else:
            self.session.add(
                TenantAppointmentType(
                    tenant_id=tenant_id,
                    location_id=location_id,
                    source=source,
                    source_id=source_id,
                    name=name,
                    duration_minutes=duration_minutes,
                    source_metadata=source_metadata,
                    synced_at=synced_at,
                )
            )

    # ── Audit helper ────────────────────────────────────────────────────

    @staticmethod
    def _audit_sync(tenant: Tenant, location: TenantLocation, result: SyncResult) -> None:
        """Fire-and-forget audit log for sync operation."""
        log_audit_background(
            actor=AuditActor.SYSTEM,
            action=AuditAction.LOCATION_SYNC,
            target_resource=f"location:{location.slug}",
            outcome=AuditOutcome.SUCCESS if result.success else AuditOutcome.FAILURE_EXTERNAL_API,
            metadata={
                "providers_synced": result.providers_synced,
                "appointment_types_synced": result.appointment_types_synced,
                "errors": result.errors[:5],
            },
            tenant_id=tenant.id,
        )
