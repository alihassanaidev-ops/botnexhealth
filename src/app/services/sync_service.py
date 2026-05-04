"""Sync service — fetches providers, appointment types, operatories, and descriptors
from PMS and caches locally."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_operatory import InstitutionOperatory
from src.app.models.institution_provider import InstitutionProvider
from src.app.pms.base import SupportsAppointmentTypeCreation
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.services.audit import log_audit_background

if TYPE_CHECKING:
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation
    from src.app.pms.base import PMSAdapter

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation for a single location."""

    location_slug: str
    providers_synced: int = 0
    appointment_types_synced: int = 0
    operatories_synced: int = 0
    descriptors_synced: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class SyncService:
    """Syncs PMS data into local cache tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def sync_location(self, institution: Institution, location: InstitutionLocation) -> SyncResult:
        """Sync all PMS data for a single location."""
        result = SyncResult(location_slug=location.slug)
        now = datetime.now(timezone.utc)

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
        except Exception as e:
            result.errors.append(f"Failed to get PMS adapter: {e}")
            self._audit_sync(institution, location, result)
            return result

        # Sync providers
        await self._sync_providers(adapter, institution.id, location.id, now, result)

        # Sync appointment types
        await self._sync_appointment_types(adapter, institution.id, location.id, now, result)

        # Sync operatories
        await self._sync_operatories(adapter, institution.id, location.id, now, result)

        # Sync descriptors (NexHealth-specific — only if adapter supports it)
        await self._sync_descriptors(adapter, institution.id, location.id, now, result)


        await self.session.flush()
        self._audit_sync(institution, location, result)
        logger.info(
            f"Sync complete for {location.slug}: "
            f"{result.providers_synced} providers, "
            f"{result.appointment_types_synced} appointment types, "
            f"{result.operatories_synced} operatories, "
            f"{result.descriptors_synced} descriptors"
        )
        return result

    async def sync_all_locations(self, institution: Institution, locations: list[InstitutionLocation]) -> dict[str, SyncResult]:
        """Sync all active locations for an institution."""
        results: dict[str, SyncResult] = {}
        for loc in locations:
            if loc.is_active:
                results[loc.slug] = await self.sync_location(institution, loc)
        return results

    # ── Sync orchestrators ─────────────────────────────────────────────

    async def _sync_providers(
        self, adapter: PMSAdapter, institution_id: str, location_id: str, now: datetime, result: SyncResult
    ) -> None:
        try:
            pms_providers = await adapter.list_providers()
            for p in pms_providers:
                await self._upsert_provider(
                    institution_id=institution_id,
                    location_id=location_id,
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
            logger.error(f"Provider sync failed: {e}")
            result.errors.append(f"Provider sync error: {e}")

    async def _sync_appointment_types(
        self, adapter: PMSAdapter, institution_id: str, location_id: str, now: datetime, result: SyncResult
    ) -> None:
        try:
            pms_types = await adapter.list_appointment_types()
            for at in pms_types:
                await self._upsert_appointment_type(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=adapter.source,
                    source_id=at.id,
                    name=at.name,
                    duration_minutes=at.duration_minutes,
                    source_metadata=at.source_metadata,
                    synced_at=now,
                )
                result.appointment_types_synced += 1
        except Exception as e:
            logger.error(f"Appointment type sync failed: {e}")
            result.errors.append(f"Appointment type sync error: {e}")

    async def _sync_operatories(
        self, adapter: PMSAdapter, institution_id: str, location_id: str, now: datetime, result: SyncResult
    ) -> None:
        try:
            pms_ops = await adapter.list_operatories()
            for op in pms_ops:
                await self._upsert_operatory(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=adapter.source,
                    source_id=op.id,
                    name=op.name,
                    is_active=op.is_active,
                    synced_at=now,
                )
                result.operatories_synced += 1
        except Exception as e:
            logger.error(f"Operatory sync failed: {e}")
            result.errors.append(f"Operatory sync error: {e}")

    async def _sync_descriptors(
        self, adapter: PMSAdapter, institution_id: str, location_id: str, now: datetime, result: SyncResult
    ) -> None:
        if not isinstance(adapter, SupportsAppointmentTypeCreation):
            return
        try:
            raw_descriptors = await adapter.list_pms_descriptors()
            for d in raw_descriptors:
                source_id = str(d.get("id", ""))
                if not source_id:
                    continue
                await self._upsert_descriptor(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=adapter.source,
                    source_id=source_id,
                    name=d.get("name", ""),
                    descriptor_type=d.get("descriptor_type"),
                    code=d.get("code"),
                    is_active=d.get("active", True),
                    source_metadata={
                        k: v for k, v in d.items()
                        if k not in ("id", "name", "descriptor_type", "code", "active")
                    } or None,
                    synced_at=now,
                )
                result.descriptors_synced += 1
        except Exception as e:
            logger.error(f"Descriptor sync failed: {e}")
            result.errors.append(f"Descriptor sync error: {e}")


    # ── Upsert helpers ──────────────────────────────────────────────────

    async def _upsert_provider(
        self,
        institution_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str | None,
        first_name: str | None,
        last_name: str | None,
        specialty: str | None,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached provider row by (institution_id, location_id, source_id)."""
        stmt = select(InstitutionProvider).where(
            InstitutionProvider.institution_id == institution_id,
            InstitutionProvider.location_id == location_id,
            InstitutionProvider.source_id == source_id,
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
                InstitutionProvider(
                    institution_id=institution_id,
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
        institution_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str,
        duration_minutes: int | None,
        source_metadata: dict | None,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached appointment type row by (institution_id, location_id, source_id)."""
        stmt = select(InstitutionAppointmentType).where(
            InstitutionAppointmentType.institution_id == institution_id,
            InstitutionAppointmentType.location_id == location_id,
            InstitutionAppointmentType.source_id == source_id,
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
                InstitutionAppointmentType(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=source,
                    source_id=source_id,
                    name=name,
                    duration_minutes=duration_minutes,
                    source_metadata=source_metadata,
                    synced_at=synced_at,
                )
            )

    async def _upsert_operatory(
        self,
        institution_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str,
        is_active: bool,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached operatory row by (institution_id, location_id, source_id)."""
        stmt = select(InstitutionOperatory).where(
            InstitutionOperatory.institution_id == institution_id,
            InstitutionOperatory.location_id == location_id,
            InstitutionOperatory.source_id == source_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.name = name
            existing.is_active = is_active
            existing.source = source
            existing.synced_at = synced_at
        else:
            self.session.add(
                InstitutionOperatory(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=source,
                    source_id=source_id,
                    name=name,
                    is_active=is_active,
                    synced_at=synced_at,
                )
            )

    async def _upsert_descriptor(
        self,
        institution_id: str,
        location_id: str,
        source: str,
        source_id: str,
        name: str,
        descriptor_type: str | None,
        code: str | None,
        is_active: bool,
        source_metadata: dict | None,
        synced_at: datetime,
    ) -> None:
        """Insert or update a cached descriptor row by (institution_id, location_id, source_id)."""
        stmt = select(InstitutionDescriptor).where(
            InstitutionDescriptor.institution_id == institution_id,
            InstitutionDescriptor.location_id == location_id,
            InstitutionDescriptor.source_id == source_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.name = name
            existing.descriptor_type = descriptor_type
            existing.code = code
            existing.is_active = is_active
            existing.source = source
            existing.source_metadata = source_metadata
            existing.synced_at = synced_at
        else:
            self.session.add(
                InstitutionDescriptor(
                    institution_id=institution_id,
                    location_id=location_id,
                    source=source,
                    source_id=source_id,
                    name=name,
                    descriptor_type=descriptor_type,
                    code=code,
                    is_active=is_active,
                    source_metadata=source_metadata,
                    synced_at=synced_at,
                )
            )


    # ── Audit helper ────────────────────────────────────────────────────

    @staticmethod
    def _audit_sync(institution: Institution, location: InstitutionLocation, result: SyncResult) -> None:
        """Fire-and-forget audit log for sync operation."""
        log_audit_background(
            actor=AuditActor.SYSTEM,
            action=AuditAction.LOCATION_SYNC,
            target_resource=f"location:{location.slug}",
            outcome=AuditOutcome.SUCCESS if result.success else AuditOutcome.FAILURE_EXTERNAL_API,
            metadata={
                "providers_synced": result.providers_synced,
                "appointment_types_synced": result.appointment_types_synced,
                "operatories_synced": result.operatories_synced,
                "descriptors_synced": result.descriptors_synced,

                "errors": result.errors[:5],
            },
            institution_id=institution.id,
        )
