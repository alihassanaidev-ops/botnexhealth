"""Initial backfill and reconciliation for NexHealth appointment projections."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.nexhealth_projection_service import (
    NexHealthProjectionService,
)

logger = logging.getLogger(__name__)

SyncMode = Literal["backfill", "reconciliation"]

_DEFAULT_LOOKAHEAD_DAYS = 90
_LOCATION_PACING_MIN_SECONDS = 0.25
_LOCATION_PACING_MAX_SECONDS = 1.0


@dataclass
class AppointmentSyncSummary:
    locations_scanned: int = 0
    appointments_seen: int = 0
    projected: int = 0
    triggered: int = 0
    cancelled_runs: int = 0
    failed_locations: int = 0


@dataclass
class PatientSyncSummary:
    locations_scanned: int = 0
    patients_seen: int = 0
    projected: int = 0
    failed_locations: int = 0


class NexHealthAppointmentSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_backfill(
        self, *, lookahead_days: int = _DEFAULT_LOOKAHEAD_DAYS
    ) -> AppointmentSyncSummary:
        return await self._run(mode="backfill", lookahead_days=lookahead_days)

    async def run_reconciliation(
        self, *, lookahead_days: int = _DEFAULT_LOOKAHEAD_DAYS
    ) -> AppointmentSyncSummary:
        return await self._run(mode="reconciliation", lookahead_days=lookahead_days)

    async def _run(self, *, mode: SyncMode, lookahead_days: int) -> AppointmentSyncSummary:
        rows = await self._load_subscription_locations()
        summary = AppointmentSyncSummary()
        for idx, row in enumerate(rows):
            if idx > 0:
                await asyncio.sleep(
                    random.uniform(_LOCATION_PACING_MIN_SECONDS, _LOCATION_PACING_MAX_SECONDS)
                )
            try:
                await self._sync_location(
                    mode=mode,
                    subscription=row.subscription,
                    institution=row.institution,
                    location=row.location,
                    lookahead_days=lookahead_days,
                    summary=summary,
                )
            except Exception as exc:  # noqa: BLE001
                summary.failed_locations += 1
                row.subscription.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                row.subscription.error_metadata = {"type": type(exc).__name__, "mode": mode}
                logger.exception(
                    "nexhealth %s failed institution=%s location=%s: %s",
                    mode,
                    row.institution.id,
                    row.location.id,
                    exc,
                )
        return summary

    async def _load_subscription_locations(self) -> list["_SubscriptionLocation"]:
        result = await self.session.execute(
            select(
                NexHealthWebhookSubscription,
                Institution,
                InstitutionLocation,
            )
            .join(Institution, Institution.id == NexHealthWebhookSubscription.institution_id)
            .join(InstitutionLocation, InstitutionLocation.id == NexHealthWebhookSubscription.location_id)
            .where(
                NexHealthWebhookSubscription.status.in_(
                    [
                        NexHealthWebhookSubscriptionStatus.ACTIVE.value,
                        NexHealthWebhookSubscriptionStatus.PENDING.value,
                    ]
                ),
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        return [
            _SubscriptionLocation(subscription=sub, institution=inst, location=loc)
            for sub, inst, loc in result.all()
        ]

    async def sync_subscription(
        self,
        *,
        subscription_id: str,
        mode: SyncMode,
        lookahead_days: int = _DEFAULT_LOOKAHEAD_DAYS,
    ) -> AppointmentSyncSummary:
        """Sync one subscription row under an institution-scoped session."""
        result = await self.session.execute(
            select(
                NexHealthWebhookSubscription,
                Institution,
                InstitutionLocation,
            )
            .join(Institution, Institution.id == NexHealthWebhookSubscription.institution_id)
            .join(InstitutionLocation, InstitutionLocation.id == NexHealthWebhookSubscription.location_id)
            .where(NexHealthWebhookSubscription.id == subscription_id)
        )
        row = result.first()
        summary = AppointmentSyncSummary()
        if row is None:
            return summary
        subscription, institution, location = row
        await self._sync_location(
            mode=mode,
            subscription=subscription,
            institution=institution,
            location=location,
            lookahead_days=lookahead_days,
            summary=summary,
        )
        return summary

    async def _sync_location(
        self,
        *,
        mode: SyncMode,
        subscription: NexHealthWebhookSubscription,
        institution: Institution,
        location: InstitutionLocation,
        lookahead_days: int,
        summary: AppointmentSyncSummary,
    ) -> None:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        today = date.today()
        end = today + timedelta(days=lookahead_days)
        adapter = await NexHealthAdapter.create(institution, location)
        try:
            appointments = await adapter.list_appointments(
                start_date=today.isoformat(),
                end_date=end.isoformat(),
            )
        finally:
            await adapter.close()

        summary.locations_scanned += 1
        summary.appointments_seen += len(appointments)
        for appt in appointments:
            projected, triggered, cancelled = await self._project_and_maybe_trigger(
                institution_id=str(institution.id),
                location_id=str(location.id),
                appointment=appt,
                mode=mode,
            )
            summary.projected += int(projected)
            summary.triggered += int(triggered)
            summary.cancelled_runs += cancelled

        now = datetime.now(timezone.utc)
        if mode == "backfill":
            subscription.last_backfill_at = now
        else:
            subscription.last_reconciliation_at = now
        subscription.updated_at = now
        if subscription.status == NexHealthWebhookSubscriptionStatus.PENDING.value:
            # A location that can be read successfully is operational even if the
            # provider subscription id is still being reconciled manually.
            subscription.error_metadata = None

    async def _project_and_maybe_trigger(
        self,
        *,
        institution_id: str,
        location_id: str,
        appointment: dict[str, Any],
        mode: SyncMode,
    ) -> tuple[bool, bool, int]:
        appointment_id = _appointment_id(appointment)
        if not appointment_id:
            return False, False, 0

        patient_id = _patient_id(appointment)
        contact_id = await self._contact_id_for_patient(
            institution_id=institution_id, patient_id=patient_id
        )
        start_time = _start_time(appointment)
        cancelled = _is_cancelled(appointment)

        upsert = await NexHealthProjectionService(self.session).upsert_appointment(
            institution_id=institution_id,
            appointment_id=appointment_id,
            location_id=location_id,
            nexhealth_patient_id=patient_id,
            contact_id=contact_id,
            start_time=start_time,
            event=f"appointment.{mode}",
            cancelled=cancelled,
            provider_id=_provider_id(appointment),
            appointment_type_id=_appointment_type_id(appointment),
        )

        cancelled_runs = 0
        if cancelled:
            cancelled_runs = await _cancel_runs_for_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                reason=f"appointment_{mode}_cancelled",
            )
            return True, False, cancelled_runs

        if not start_time:
            return True, False, 0

        if upsert.change == "rescheduled":
            cancelled_runs = await _cancel_runs_for_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                reason=f"appointment_{mode}_rescheduled",
            )

        should_trigger = upsert.change in {"new", "rescheduled"}
        if should_trigger:
            _trigger_appointment_workflows(
                institution_id=institution_id,
                appointment_id=appointment_id,
                appointment_at_iso=start_time,
                contact_id=contact_id,
                location_id=location_id,
                trigger_metadata={
                    "event": f"appointment.{mode}",
                    "nexhealth_appointment_id": appointment_id,
                    "nexhealth_location_id": appointment.get("location_id"),
                    "source": mode,
                },
            )
        return True, should_trigger, cancelled_runs

    async def _contact_id_for_patient(
        self, *, institution_id: str, patient_id: str | None
    ) -> str | None:
        if not patient_id:
            return None
        result = await self.session.execute(
            select(Contact).where(
                Contact.institution_id == institution_id,
                Contact.nexhealth_patient_id == patient_id,
            )
        )
        contact = result.scalar_one_or_none()
        return str(contact.id) if contact else None


class NexHealthPatientSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_backfill(self) -> PatientSyncSummary:
        return await self._run(mode="backfill")

    async def run_reconciliation(self) -> PatientSyncSummary:
        return await self._run(mode="reconciliation")

    async def _run(self, *, mode: SyncMode) -> PatientSyncSummary:
        rows = await NexHealthAppointmentSyncService(self.session)._load_subscription_locations()
        summary = PatientSyncSummary()
        for idx, row in enumerate(rows):
            if idx > 0:
                await asyncio.sleep(
                    random.uniform(_LOCATION_PACING_MIN_SECONDS, _LOCATION_PACING_MAX_SECONDS)
                )
            try:
                await self._sync_location(
                    mode=mode,
                    subscription=row.subscription,
                    institution=row.institution,
                    location=row.location,
                    summary=summary,
                )
            except Exception as exc:  # noqa: BLE001
                summary.failed_locations += 1
                row.subscription.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                row.subscription.error_metadata = {
                    "type": type(exc).__name__,
                    "mode": f"patient_{mode}",
                }
                logger.exception(
                    "nexhealth patient %s failed institution=%s location=%s: %s",
                    mode,
                    row.institution.id,
                    row.location.id,
                    exc,
                )
        return summary

    async def sync_subscription(
        self,
        *,
        subscription_id: str,
        mode: SyncMode,
    ) -> PatientSyncSummary:
        result = await self.session.execute(
            select(
                NexHealthWebhookSubscription,
                Institution,
                InstitutionLocation,
            )
            .join(Institution, Institution.id == NexHealthWebhookSubscription.institution_id)
            .join(
                InstitutionLocation,
                InstitutionLocation.id == NexHealthWebhookSubscription.location_id,
            )
            .where(NexHealthWebhookSubscription.id == subscription_id)
        )
        row = result.first()
        summary = PatientSyncSummary()
        if row is None:
            return summary
        subscription, institution, location = row
        await self._sync_location(
            mode=mode,
            subscription=subscription,
            institution=institution,
            location=location,
            summary=summary,
        )
        return summary

    async def _sync_location(
        self,
        *,
        mode: SyncMode,
        subscription: NexHealthWebhookSubscription,
        institution: Institution,
        location: InstitutionLocation,
        summary: PatientSyncSummary,
    ) -> None:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        updated_since = _patient_updated_since(subscription) if mode == "reconciliation" else None
        adapter = await NexHealthAdapter.create(institution, location)
        try:
            patients = await adapter.list_patients(
                updated_since=updated_since.isoformat() if updated_since else None
            )
        finally:
            await adapter.close()

        summary.locations_scanned += 1
        summary.patients_seen += len(patients)
        for patient in patients:
            if await self._project_patient(
                institution_id=str(institution.id),
                location=location,
                patient=patient,
                mode=mode,
            ):
                summary.projected += 1

        now = datetime.now(timezone.utc)
        if mode == "backfill":
            subscription.last_patient_backfill_at = now
        else:
            subscription.last_patient_reconciliation_at = now
        subscription.updated_at = now
        if subscription.status == NexHealthWebhookSubscriptionStatus.PENDING.value:
            subscription.error_metadata = None

    async def _project_patient(
        self,
        *,
        institution_id: str,
        location: InstitutionLocation,
        patient: dict[str, Any],
        mode: SyncMode,
    ) -> bool:
        patient_id = _patient_record_id(patient)
        if not patient_id:
            return False
        nexhealth_location_ids = _patient_location_ids(patient) or [
            str(location.nexhealth_location_id)
        ]
        local_location_ids = await self._local_location_ids_for_patient(
            institution_id=institution_id,
            fallback_location_id=str(location.id),
            nexhealth_location_ids=nexhealth_location_ids,
            subdomain=str(location.nexhealth_subdomain),
        )
        await NexHealthProjectionService(self.session).upsert_patient(
            institution_id=institution_id,
            patient=patient,
            local_location_ids=local_location_ids,
            nexhealth_location_ids=nexhealth_location_ids,
            event=f"patient.{mode}",
        )
        return True

    async def _local_location_ids_for_patient(
        self,
        *,
        institution_id: str,
        fallback_location_id: str,
        nexhealth_location_ids: list[str],
        subdomain: str,
    ) -> list[str]:
        result = await self.session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.institution_id == institution_id,
                InstitutionLocation.nexhealth_subdomain == subdomain,
                InstitutionLocation.nexhealth_location_id.in_(nexhealth_location_ids),
            )
        )
        location_ids = [str(location.id) for location in result.scalars().all()]
        return location_ids or [fallback_location_id]


@dataclass
class _SubscriptionLocation:
    subscription: NexHealthWebhookSubscription
    institution: Institution
    location: InstitutionLocation


def _appointment_id(appt: dict[str, Any]) -> str | None:
    value = appt.get("id") or appt.get("appointment_id")
    return str(value) if value not in (None, "") else None


def _patient_id(appt: dict[str, Any]) -> str | None:
    value = appt.get("patient_id")
    if value is None and isinstance(appt.get("patient"), dict):
        value = appt["patient"].get("id")
    return str(value) if value not in (None, "") else None


def _patient_record_id(patient: dict[str, Any]) -> str | None:
    value = patient.get("id")
    return str(value) if value not in (None, "") else None


def _patient_location_ids(patient: dict[str, Any]) -> list[str]:
    values = patient.get("location_ids") or patient.get("locations") or []
    ids: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                value = value.get("id") or value.get("location_id")
            if value not in (None, ""):
                ids.append(str(value))
    value = patient.get("location_id")
    if value not in (None, ""):
        ids.append(str(value))
    return sorted(set(ids))


def _patient_updated_since(subscription: NexHealthWebhookSubscription) -> datetime | None:
    watermark = (
        getattr(subscription, "last_patient_reconciliation_at", None)
        or getattr(subscription, "last_patient_backfill_at", None)
    )
    if watermark is None:
        return None
    if watermark.tzinfo is None:
        watermark = watermark.replace(tzinfo=timezone.utc)
    return watermark - timedelta(hours=1)


def _provider_id(appt: dict[str, Any]) -> str | None:
    value = appt.get("provider_id")
    if value is None and isinstance(appt.get("provider"), dict):
        value = appt["provider"].get("id")
    return str(value) if value not in (None, "") else None


def _appointment_type_id(appt: dict[str, Any]) -> str | None:
    value = appt.get("appointment_type_id")
    if value is None and isinstance(appt.get("appointment_type"), dict):
        value = appt["appointment_type"].get("id")
    return str(value) if value not in (None, "") else None


def _start_time(appt: dict[str, Any]) -> str | None:
    value = appt.get("start_time") or appt.get("start")
    return str(value) if value not in (None, "") else None


def _is_cancelled(appt: dict[str, Any]) -> bool:
    return bool(appt.get("cancelled", False) or appt.get("canceled", False))


async def _cancel_runs_for_appointment(
    *, institution_id: str, appointment_id: str, reason: str
) -> int:
    from src.app.api.routes.nexhealth_webhooks import _cancel_runs_for_appointment as cancel

    return await cancel(institution_id, appointment_id, reason=reason)


def _trigger_appointment_workflows(
    *,
    institution_id: str,
    appointment_id: str,
    appointment_at_iso: str,
    contact_id: str | None,
    location_id: str,
    trigger_metadata: dict[str, Any],
) -> None:
    from src.app.tasks.automation_workflow import trigger_appointment_workflows

    trigger_appointment_workflows.delay(
        institution_id=institution_id,
        appointment_id=appointment_id,
        appointment_at_iso=appointment_at_iso,
        contact_id=contact_id,
        location_id=location_id,
        trigger_metadata=trigger_metadata,
    )
