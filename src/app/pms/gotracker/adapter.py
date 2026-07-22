"""GoTracker Synchronizer PMS adapter.

This adapter talks to the ScaleNexus GoTracker Synchronizer API. It only
depends on the public synchronizer contract; the on-site agent, SQL sync, write
queue, and installer remain outside this repository.
"""

from __future__ import annotations

from typing import Any

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.base import PMSAdapter, SupportsAppointmentConfirmation
from src.app.pms.gotracker import mappers
from src.app.pms.gotracker.client import GoTrackerAPIError, GoTrackerClient
from src.app.pms.models import (
    BookingRequest,
    BookingResult,
    PatientCreateRequest,
    SetupStep,
    SlotSearchResult,
    UniversalAppointmentType,
    UniversalLocation,
    UniversalOperatory,
    UniversalPatient,
    UniversalProvider,
    UniversalSlot,
)


class GoTrackerAdapter(PMSAdapter, SupportsAppointmentConfirmation):
    source = "gotracker"

    def __init__(
        self,
        client: GoTrackerClient,
        institution: Institution,
        location: InstitutionLocation,
    ) -> None:
        self._client = client
        self._institution = institution
        self._location = location

    @classmethod
    async def create(
        cls, institution: Institution, location: InstitutionLocation
    ) -> "GoTrackerAdapter":
        from src.app.config import settings

        product_key = location.gotracker_product_key
        if not product_key:
            raise ValueError(
                f"Location {location.slug} is missing gotracker_product_key; "
                "cannot create PMS adapter"
            )

        base_url = location.gotracker_base_url or settings.gotracker_base_url
        if not base_url:
            raise RuntimeError("GOTRACKER_BASE_URL is not configured")

        return cls(
            GoTrackerClient(base_url=base_url, product_key=product_key),
            institution,
            location,
        )

    async def close(self) -> None:
        await self._client.close()

    # ── Patients ─────────────────────────────────────────────────────────

    async def search_patients(self, query: str, **kwargs: Any) -> list[UniversalPatient]:
        patients = await self.list_patients(max_items=200)
        needle_values = [
            query,
            kwargs.get("email"),
            kwargs.get("phone_number"),
            kwargs.get("name"),
        ]
        needles = [str(value).lower() for value in needle_values if value]
        if not needles:
            return [mappers.to_patient(row) for row in patients[:10]]

        matches = []
        for row in patients:
            haystack = " ".join(
                str(value).lower()
                for value in (
                    row.get("FirstName"),
                    row.get("LastName"),
                    row.get("Email"),
                    row.get("Phone"),
                    row.get("PhoneNumber"),
                    row.get("CellPhone"),
                    row.get("ContactId"),
                )
                if value
            )
            if any(needle in haystack for needle in needles):
                matches.append(row)
        return [mappers.to_patient(row) for row in matches[:10]]

    async def list_patients(
        self,
        *,
        updated_since: str | None = None,
        max_items: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if updated_since:
            params["since"] = updated_since
        return await self._fetch_all(
            "GET", "/api/patients/getAllContacts", params=params, max_items=max_items
        )

    async def create_patient(self, req: PatientCreateRequest) -> dict[str, Any]:
        return {
            "success": False,
            "patient_id": None,
            "message": (
                "GoTracker patient creation is handled by the on-site synchronizer "
                "agent; API patient upsert is not available through the product key."
            ),
        }

    # ── Appointment Types ────────────────────────────────────────────────

    async def list_appointment_types(self) -> list[UniversalAppointmentType]:
        raw = await self._client.request("GET", "/api/appointment_types")
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [mappers.to_appointment_type(item) for item in data]

    # ── Providers ────────────────────────────────────────────────────────

    async def list_providers(self) -> list[UniversalProvider]:
        raw = await self._client.request("GET", "/api/providers/getAllProviders")
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [mappers.to_provider(item) for item in data]

    # ── Operatories ──────────────────────────────────────────────────────

    async def list_operatories(self) -> list[UniversalOperatory]:
        raw = await self._client.request("GET", "/api/scheduling/operatories")
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [mappers.to_operatory(item) for item in data]

    # ── Slots ────────────────────────────────────────────────────────────

    async def get_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | list[str] | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
    ) -> list[UniversalSlot]:
        result = await self.find_available_slots(
            start_date=start_date,
            days=days,
            provider_id=provider_id,
            appointment_type_id=appointment_type_id,
            operatory_ids=operatory_ids,
        )
        return result.slots

    async def find_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | list[str] | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
    ) -> SlotSearchResult:
        params: dict[str, Any] = {"start_date": start_date, "days": days}
        if provider_id:
            raw_provider_ids = (
                [mappers.strip(item) for item in provider_id]
                if isinstance(provider_id, list)
                else [mappers.strip(provider_id)]
            )
            params["provider_ids"] = ",".join(str(item) for item in raw_provider_ids if item)
        if appointment_type_id:
            params["appointment_type_id"] = mappers.strip(appointment_type_id)
        if operatory_ids:
            params["operatory_ids"] = ",".join(
                str(item) for item in (mappers.strip(value) for value in operatory_ids)
                if item
            )

        raw = await self._client.request(
            "GET", "/api/scheduling/available_slots", params=params
        )
        slots: list[UniversalSlot] = []
        next_by_provider: dict[str, str] = {}
        for group in raw.get("data") or []:
            group_pid = group.get("pid") or group.get("provider_id")
            group_lid = group.get("lid") or group.get("location_id")
            next_date = group.get("next_available_date")
            if next_date and group_pid is not None:
                next_by_provider[mappers.pid(group_pid)] = next_date
            for slot in group.get("slots") or []:
                slots.append(
                    mappers.to_slot(
                        slot,
                        provider_id=group_pid,
                        location_id=group_lid,
                        appointment_type_id=appointment_type_id,
                    )
                )

        earliest = min(next_by_provider.values()) if next_by_provider else None
        return SlotSearchResult(
            slots=slots,
            next_available_date=earliest,
            next_available_by_provider=next_by_provider,
        )

    # ── Appointment Queries ─────────────────────────────────────────────

    async def has_provider_appointments_on_date(
        self, provider_id: str, date_str: str
    ) -> bool:
        appointments = await self.list_appointments(
            start_date=f"{date_str}T00:00:00+0000",
            end_date=f"{date_str}T23:59:59+0000",
            max_items=500,
        )
        raw_provider_id = mappers.strip(provider_id)
        for appt in appointments:
            appt_provider = appt.get("ProviderId") or appt.get("provider_id")
            cancelled = bool(appt.get("cancelled") or appt.get("Cancelled"))
            if str(appt_provider) == str(raw_provider_id) and not cancelled:
                return True
        return False

    async def list_appointments(
        self,
        *,
        start_date: str,
        end_date: str,
        max_items: int = 1000,
    ) -> list[dict[str, Any]]:
        return await self._fetch_all(
            "GET",
            "/api/appointments/getAllAppointments",
            params={"start": start_date, "end": end_date},
            max_items=max_items,
        )

    async def list_patient_recalls(self, *, max_items: int = 500) -> list[dict[str, Any]]:
        return await self._fetch_all(
            "GET",
            "/api/patients/recalls",
            params={"overdue_only": "true"},
            max_items=max_items,
        )

    # ── Booking ──────────────────────────────────────────────────────────

    async def book_appointment(self, req: BookingRequest) -> BookingResult:
        body: dict[str, Any] = {
            "patient_id": mappers.strip(req.patient_id),
            "provider_id": mappers.strip(req.provider_id),
            "start_time": req.slot_start,
        }
        if req.operatory_id:
            body["operatory_id"] = mappers.strip(req.operatory_id)
        if req.appointment_type_id:
            body["appointment_type_id"] = mappers.strip(req.appointment_type_id)
        if req.slot_end:
            body["end_time"] = req.slot_end
        if req.note:
            body["note"] = req.note

        try:
            raw = await self._client.request("POST", "/api/appointments/book", json=body)
            result = mappers.to_booking_result(raw, success=True)
            if not result.appointment_type_id and req.appointment_type_id:
                result.appointment_type_id = req.appointment_type_id
            return result
        except GoTrackerAPIError as exc:
            return BookingResult(
                success=False,
                source="gotracker",
                status="error",
                error=str(exc),
            )

    async def cancel_appointment(self, appointment_id: str) -> BookingResult:
        return await self._set_appointment_status(
            appointment_id,
            {"cancelled": True},
            success_status="cancelled",
            success_message="Appointment cancelled successfully.",
        )

    async def confirm_appointment(self, appointment_id: str) -> BookingResult:
        return await self._set_appointment_status(
            appointment_id,
            {"confirmed": True},
            success_status="confirmed",
            success_message="Appointment confirmed successfully.",
        )

    async def reschedule_appointment(
        self, old_appointment_id: str, new_booking: BookingRequest
    ) -> BookingResult:
        book_result = await self.book_appointment(new_booking)
        if not book_result.success:
            return book_result

        cancel_result = await self.cancel_appointment(old_appointment_id)
        if not cancel_result.success:
            book_result.message = (
                "Rescheduled (new booked) but failed to cancel old appointment: "
                f"{cancel_result.error}. Please cancel manually."
            )
        else:
            book_result.message = "Rescheduled successfully (new booked, old cancelled)."
        return book_result

    # ── Locations ────────────────────────────────────────────────────────

    async def list_locations(self) -> list[UniversalLocation]:
        return [self._local_universal_location()]

    async def get_location(self, location_id: str) -> UniversalLocation | None:
        local = self._local_universal_location()
        local_ids = {mappers.strip(local.id), str(self._location.id)}
        return local if mappers.strip(location_id) in local_ids else None

    # ── Setup ────────────────────────────────────────────────────────────

    async def get_setup_steps(self) -> list[SetupStep]:
        return [
            SetupStep(
                id="connect_synchronizer",
                label="Connect GoTracker Synchronizer",
                description="Install the on-site synchronizer and configure this location's product key",
                completed=bool(self._location.gotracker_product_key_encrypted),
            ),
            SetupStep(
                id="sync_resources",
                label="Sync providers and schedule resources",
                description="Confirm providers, operatories, working hours, and appointment types are reporting",
            ),
        ]

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _set_appointment_status(
        self,
        appointment_id: str,
        body: dict[str, Any],
        *,
        success_status: str,
        success_message: str,
    ) -> BookingResult:
        try:
            await self._client.request(
                "PATCH",
                f"/api/appointments/{mappers.strip(appointment_id)}/status",
                json=body,
            )
            return BookingResult(
                success=True,
                source="gotracker",
                status=success_status,
                message=success_message,
            )
        except GoTrackerAPIError as exc:
            return BookingResult(
                success=False,
                source="gotracker",
                status="error",
                error=str(exc),
            )

    async def _fetch_all(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        per_page = 200
        page = 1
        while len(items) < max_items:
            raw = await self._client.request(
                method,
                path,
                params={**(params or {}), "page": page},
            )
            data = raw.get("data") if isinstance(raw.get("data"), list) else []
            items.extend(data[: max_items - len(items)])
            if len(data) < per_page:
                break
            page += 1
        return items

    def _local_universal_location(self) -> UniversalLocation:
        return UniversalLocation(
            id=mappers.pid(self._location.id),
            source="gotracker",
            name=self._location.name,
            address=self._location.address,
            city=self._location.city,
            phone=self._location.phone,
            timezone=self._location.timezone,
            hours=None,
        )
