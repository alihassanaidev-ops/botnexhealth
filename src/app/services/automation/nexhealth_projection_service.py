"""Appointment working-set projection + NexHealth event-ledger claim (Plan 09).

Two responsibilities, both keyed to the appointment working set:

* ``claim_event`` — event-level idempotency at webhook receipt (D-4). A redelivery
  of the same logical event is recognised here instead of re-running the trigger.
* ``upsert_appointment`` — maintain the disposable projection and classify the
  change (new / rescheduled / unchanged / cancelled) so the webhook can re-enroll
  on a reschedule (D-1) and revalidation can trust a fresh row (D-2).

Callable under both the webhook session context ('nexhealth_webhooks') and Celery
('celery') — the RLS policy on both tables allows those contexts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.nexhealth_webhook_event import (
    NexHealthWebhookEvent,
    NexHealthWebhookStatus,
)
from src.app.models.patient_working_set import PatientWorkingSet

logger = logging.getLogger(__name__)

ChangeKind = Literal["new", "rescheduled", "unchanged", "cancelled"]
PatientChangeKind = Literal["new", "updated", "unchanged"]

# A PROCESSING claim older than this is assumed abandoned (crashed worker) and
# may be reclaimed by a redelivery.
_PROCESSING_TTL_SECONDS = 300


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs((a - b).total_seconds()) < 1.0


@dataclass
class UpsertResult:
    row: AppointmentWorkingSet
    change: ChangeKind
    previous_start_time: datetime | None


@dataclass
class PatientUpsertResult:
    row: PatientWorkingSet
    contact: Contact
    change: PatientChangeKind


class NexHealthProjectionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_event(
        self,
        *,
        institution_id: str,
        appointment_id: str | None = None,
        patient_id: str | None = None,
        event_type: str,
        dedup_key: str,
    ) -> bool:
        """Claim an event for processing. Returns False if already seen (skip).

        Race-safe: the unique (institution_id, dedup_key) constraint turns a
        concurrent/replayed delivery into an IntegrityError we treat as "already
        claimed". A previously FAILED event is re-claimable (retry).
        """
        existing = (
            await self.session.execute(
                select(NexHealthWebhookEvent).where(
                    NexHealthWebhookEvent.institution_id == institution_id,
                    NexHealthWebhookEvent.dedup_key == dedup_key,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            now = datetime.now(timezone.utc)
            is_stale_processing = (
                existing.status == NexHealthWebhookStatus.PROCESSING.value
                and existing.updated_at is not None
                and (now - _as_utc(existing.updated_at)).total_seconds() > _PROCESSING_TTL_SECONDS
            )
            if existing.status == NexHealthWebhookStatus.FAILED.value or is_stale_processing:
                # Retry a failed event, or reclaim a PROCESSING row abandoned by a
                # crashed worker so a redelivery is not blocked forever.
                existing.status = NexHealthWebhookStatus.PROCESSING.value
                existing.attempts += 1
                existing.updated_at = now
                return True
            return False

        event = NexHealthWebhookEvent(
            institution_id=institution_id,
            nexhealth_appointment_id=appointment_id,
            nexhealth_patient_id=patient_id,
            event_type=event_type,
            dedup_key=dedup_key,
            status=NexHealthWebhookStatus.PROCESSING.value,
            attempts=1,
        )
        self.session.add(event)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            return False
        return True

    async def complete_event(self, *, institution_id: str, dedup_key: str, error: str | None = None) -> None:
        row = (
            await self.session.execute(
                select(NexHealthWebhookEvent).where(
                    NexHealthWebhookEvent.institution_id == institution_id,
                    NexHealthWebhookEvent.dedup_key == dedup_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = (
            NexHealthWebhookStatus.FAILED.value if error
            else NexHealthWebhookStatus.COMPLETED.value
        )
        row.last_error = error
        row.updated_at = datetime.now(timezone.utc)

    async def upsert_appointment(
        self,
        *,
        institution_id: str,
        appointment_id: str,
        location_id: str | None,
        nexhealth_patient_id: str | None,
        contact_id: str | None,
        start_time: str | None,
        event: str,
        cancelled: bool,
        provider_id: str | None = None,
        appointment_type_id: str | None = None,
    ) -> UpsertResult:
        """UPSERT the projection row and classify the change vs the stored state."""
        incoming_start = _parse_dt(start_time)
        now = datetime.now(timezone.utc)

        row = (
            await self.session.execute(
                select(AppointmentWorkingSet).where(
                    AppointmentWorkingSet.institution_id == institution_id,
                    AppointmentWorkingSet.nexhealth_appointment_id == appointment_id,
                )
            )
        ).scalar_one_or_none()

        new_status = "cancelled" if cancelled else "scheduled"

        if row is None:
            row = AppointmentWorkingSet(
                id=str(uuid4()),
                institution_id=institution_id,
                location_id=location_id,
                nexhealth_appointment_id=appointment_id,
                nexhealth_patient_id=nexhealth_patient_id,
                contact_id=contact_id,
                provider_id=provider_id,
                appointment_type_id=appointment_type_id,
                start_time=incoming_start,
                status=new_status,
                last_event=event,
                last_synced_at=now,
            )
            self.session.add(row)
            change: ChangeKind = "cancelled" if cancelled else "new"
            return UpsertResult(row=row, change=change, previous_start_time=None)

        prev_start = row.start_time
        prev_status = row.status

        # Update stored state.
        row.location_id = location_id or row.location_id
        row.nexhealth_patient_id = nexhealth_patient_id or row.nexhealth_patient_id
        row.contact_id = contact_id or row.contact_id
        row.provider_id = provider_id or getattr(row, "provider_id", None)
        row.appointment_type_id = appointment_type_id or getattr(row, "appointment_type_id", None)
        row.last_event = event
        row.last_synced_at = now
        row.updated_at = now
        row.status = new_status
        if incoming_start is not None:
            row.start_time = incoming_start

        if cancelled:
            change = "cancelled"
        elif prev_status == "cancelled":
            # Re-activated (uncancelled) — treat as new scheduling.
            change = "rescheduled" if not _same_instant(prev_start, incoming_start) else "new"
        elif incoming_start is not None and not _same_instant(prev_start, incoming_start):
            change = "rescheduled"
        else:
            change = "unchanged"

        return UpsertResult(row=row, change=change, previous_start_time=prev_start)

    async def upsert_patient(
        self,
        *,
        institution_id: str,
        patient: dict[str, Any],
        local_location_ids: list[str],
        nexhealth_location_ids: list[str],
        event: str,
    ) -> PatientUpsertResult:
        """Refresh local contact + patient projection from a NexHealth patient payload."""
        patient_id = _clean_str(patient.get("id"))
        if not patient_id:
            raise ValueError("patient payload missing id")

        bio = patient.get("bio") if isinstance(patient.get("bio"), dict) else {}
        first_name = _clean_str(patient.get("first_name"))
        last_name = _clean_str(patient.get("last_name"))
        full_name = _clean_str(patient.get("name")) or _join_name(first_name, last_name)
        email = _clean_str(patient.get("email")) if "email" in patient else None
        phone = _patient_phone(bio)
        dob = _clean_str(bio.get("date_of_birth")) if "date_of_birth" in bio else None
        inactive = bool(patient.get("inactive", False))
        unsubscribe_sms = bool(patient.get("unsubscribe_sms", False))
        preferred_language = _clean_str(patient.get("preferred_language"))
        is_new_patient = bool(bio.get("new_patient", False))
        now = datetime.now(timezone.utc)

        contact = (
            await self.session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    Contact.nexhealth_patient_id == patient_id,
                )
            )
        ).scalar_one_or_none()

        contact_created = False
        if contact is None:
            contact = Contact(
                institution_id=institution_id,
                nexhealth_patient_id=patient_id,
                first_name=first_name,
                last_name=last_name,
                full_name=full_name,
                is_new_patient=is_new_patient,
            )
            contact_created = True
            self.session.add(contact)
        else:
            if first_name is not None:
                contact.first_name = first_name
            if last_name is not None:
                contact.last_name = last_name
            if full_name is not None:
                contact.full_name = full_name
            contact.is_new_patient = is_new_patient
            contact.anonymized_at = None
            contact.updated_at = now

        if "email" in patient:
            contact.email = email
        if _patient_has_phone_key(bio):
            contact.phone = phone
        if "date_of_birth" in bio:
            contact.date_of_birth = dob

        await self.session.flush()

        for location_id in local_location_ids:
            await self.session.execute(
                pg_insert(ContactLocationAccess)
                .values(
                    institution_id=institution_id,
                    contact_id=str(contact.id),
                    location_id=location_id,
                )
                .on_conflict_do_nothing(index_elements=["contact_id", "location_id"])
            )

        row = (
            await self.session.execute(
                select(PatientWorkingSet).where(
                    PatientWorkingSet.institution_id == institution_id,
                    PatientWorkingSet.nexhealth_patient_id == patient_id,
                )
            )
        ).scalar_one_or_none()

        primary_location_id = local_location_ids[0] if local_location_ids else None
        if row is None:
            row = PatientWorkingSet(
                institution_id=institution_id,
                primary_location_id=primary_location_id,
                contact_id=str(contact.id),
                nexhealth_patient_id=patient_id,
                nexhealth_location_ids=nexhealth_location_ids,
                first_name=first_name,
                last_name=last_name,
                full_name=full_name,
                preferred_language=preferred_language,
                inactive=inactive,
                unsubscribe_sms=unsubscribe_sms,
                is_new_patient=is_new_patient,
                last_event=event,
                last_synced_at=now,
            )
            self.session.add(row)
            change: PatientChangeKind = "new"
        else:
            changed = (
                row.contact_id != str(contact.id)
                or row.primary_location_id != primary_location_id
                or row.nexhealth_location_ids != nexhealth_location_ids
                or row.first_name != first_name
                or row.last_name != last_name
                or row.full_name != full_name
                or row.preferred_language != preferred_language
                or row.inactive != inactive
                or row.unsubscribe_sms != unsubscribe_sms
                or row.is_new_patient != is_new_patient
            )
            row.primary_location_id = primary_location_id or row.primary_location_id
            row.contact_id = str(contact.id)
            row.nexhealth_location_ids = nexhealth_location_ids
            row.first_name = first_name
            row.last_name = last_name
            row.full_name = full_name
            row.preferred_language = preferred_language
            row.inactive = inactive
            row.unsubscribe_sms = unsubscribe_sms
            row.is_new_patient = is_new_patient
            row.last_event = event
            row.last_synced_at = now
            row.updated_at = now
            change = "updated" if changed or contact_created else "unchanged"

        return PatientUpsertResult(row=row, contact=contact, change=change)


def _clean_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _join_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(part for part in (first_name, last_name) if part)
    return name or None


def _patient_has_phone_key(bio: dict[str, Any]) -> bool:
    return any(
        key in bio
        for key in ("phone_number", "cell_phone_number", "home_phone_number", "work_phone_number")
    )


def _patient_phone(bio: dict[str, Any]) -> str | None:
    for key in ("phone_number", "cell_phone_number", "home_phone_number", "work_phone_number"):
        value = _clean_str(bio.get(key))
        if value:
            return value
    return None
