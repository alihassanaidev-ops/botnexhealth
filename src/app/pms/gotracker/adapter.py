"""GoTracker Synchronizer PMS adapter.

This adapter talks to the ScaleNexus GoTracker Synchronizer API. It only
depends on the public synchronizer contract; the on-site agent, SQL sync, write
queue, and installer remain outside this repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.base import (
    PMSAdapter,
    SupportsAppointmentConfirmation,
    SupportsAppointmentTypeCreation,
    SupportsWorkingWindowOverrides,
)
from src.app.pms.gotracker import mappers
from src.app.pms.gotracker.client import GoTrackerAPIError, GoTrackerClient
from src.app.pms.gotracker.statuses import is_non_attending_status
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


class GoTrackerAdapter(
    PMSAdapter,
    SupportsAppointmentConfirmation,
    SupportsAppointmentTypeCreation,
    SupportsWorkingWindowOverrides,
):
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
        """Create a patient through the Synchronizer's consumer write-back API."""
        body = {
            "first_name": req.first_name,
            "last_name": req.last_name,
            "email": req.email,
            "phone_number": req.phone,
            "date_of_birth": req.date_of_birth,
            "provider_id": mappers.strip(req.provider_id),
            "gender": req.gender,
        }
        try:
            raw = await self._client.request("POST", "/api/patients/", json=body)
        except GoTrackerAPIError as exc:
            return {
                "success": False,
                "patient_id": None,
                "message": str(exc),
            }

        data = _data_object(raw)
        contact_id = _first(data, "ContactId", "contact_id", "id", "patient_id")
        if contact_id is None:
            return {
                "success": False,
                "patient_id": None,
                "message": "GoTracker did not return a created patient ID.",
            }
        first_name = str(_first(data, "FirstName", "first_name") or req.first_name)
        return {
            "success": True,
            "patient_id": mappers.pid(contact_id),
            "message": f"Patient {first_name} created successfully.",
        }

    async def get_patient(
        self,
        patient_id: str,
        include: list[str] | None = None,
    ) -> UniversalPatient | None:
        """Return only a verified patient's future appointment context.

        Retell performs identity verification from ``search_patients`` before
        this method is called.  The follow-up read deliberately avoids fetching
        patient demographics again and asks the Synchronizer only for this
        contact's appointments from the clinic's current day onward.
        """
        del include
        raw_patient_id = mappers.strip(patient_id)
        if not raw_patient_id:
            return None

        appointments = await self.list_appointments(
            contact_id=raw_patient_id,
            from_date=self._local_today(),
            exclude_cancelled=True,
            max_items=100,
        )
        upcoming = [
            mappers.to_upcoming_appointment(appointment)
            for appointment in appointments
            if not _is_non_attending_appointment(appointment)
        ]
        return UniversalPatient(
            id=mappers.pid(raw_patient_id),
            source=self.source,
            first_name="",
            last_name="",
            extra={"upcoming_appointments": upcoming},
        )

    # ── Appointment Types ────────────────────────────────────────────────

    async def list_appointment_types(self) -> list[UniversalAppointmentType]:
        raw = await self._client.request("GET", "/api/appointment_types")
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [mappers.to_appointment_type(item) for item in data]

    async def list_pms_descriptors(self) -> list[dict]:
        return []

    async def create_appointment_type(
        self,
        name: str,
        duration_minutes: int,
        descriptor_ids: list[str],
        *,
        provider_ids: list[str] | None = None,
        operatory_ids: list[str] | None = None,
        bookable_online: bool | None = None,
    ) -> UniversalAppointmentType:
        del descriptor_ids
        body = {
            "name": name,
            "minutes": duration_minutes,
            "bookable_online": True if bookable_online is None else bookable_online,
            "provider_ids": _strip_ids(provider_ids),
            "operatory_ids": _strip_ids(operatory_ids),
        }
        raw = await self._client.request("POST", "/api/appointment_types", json=body)
        return mappers.to_appointment_type(_data_object(raw))

    async def update_appointment_type(
        self,
        appointment_type_id: str,
        name: str | None = None,
        duration_minutes: int | None = None,
        descriptor_ids: list[str] | None = None,
        provider_ids: list[str] | None = None,
        operatory_ids: list[str] | None = None,
        bookable_online: bool | None = None,
    ) -> UniversalAppointmentType:
        del descriptor_ids
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if duration_minutes is not None:
            body["minutes"] = duration_minutes
        if bookable_online is not None:
            body["bookable_online"] = bookable_online
        if provider_ids is not None:
            body["provider_ids"] = _strip_ids(provider_ids)
        if operatory_ids is not None:
            body["operatory_ids"] = _strip_ids(operatory_ids)

        raw_id = mappers.strip(appointment_type_id)
        raw = await self._client.request(
            "PATCH",
            f"/api/appointment_types/{raw_id}",
            json=body,
        )
        return mappers.to_appointment_type(_data_object(raw, fallback_id=raw_id))

    async def delete_appointment_type(self, appointment_type_id: str) -> None:
        raw_id = mappers.strip(appointment_type_id)
        await self._client.request("DELETE", f"/api/appointment_types/{raw_id}")

    # ── Providers ────────────────────────────────────────────────────────

    async def list_providers(self) -> list[UniversalProvider]:
        raw = await self._client.request("GET", "/api/providers/getAllProviders")
        data = _list_data(raw, nested_key="providers")
        return [mappers.to_provider(item) for item in data]

    # ── Operatories ──────────────────────────────────────────────────────

    async def list_operatories(self) -> list[UniversalOperatory]:
        raw = await self._client.request("GET", "/api/scheduling/operatories")
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [mappers.to_operatory(item) for item in data]

    # ── Working windows ─────────────────────────────────────────────────

    async def list_availabilities(self, **kwargs: Any) -> list[dict]:
        """List synced Tracker working windows, including their cloud override.

        A working window is not a derived bookable slot.  ``working_window_id``
        is stable across synchronizer refreshes and is the only ID accepted by
        the override mutation endpoints.
        """
        provider_id = kwargs.pop("provider_id", None)
        operatory_ids = kwargs.pop("operatory_ids", None)
        kwargs.pop("ignore_past_dates", None)
        start_date = kwargs.pop("start_date", None) or self._local_today()
        days = int(kwargs.pop("days", 7))
        if not 1 <= days <= 60:
            raise ValueError("GoTracker working-window days must be between 1 and 60.")

        params: dict[str, Any] = {"start_date": start_date, "days": days}
        if provider_id:
            params["provider_ids"] = str(mappers.strip(provider_id))
        if operatory_ids:
            raw_ids = [mappers.strip(value) for value in operatory_ids]
            params["operatory_ids"] = ",".join(
                str(value) for value in raw_ids if value
            )

        raw = await self._client.request(
            "GET", "/api/scheduling/working_hours", params=params
        )
        rows = raw.get("data") if isinstance(raw.get("data"), list) else []
        return [_to_working_window(row) for row in rows if isinstance(row, dict)]

    async def update_availability(
        self,
        availability_id: str,
        appointment_type_ids: list[str] | None = None,
        days: list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        operatory_id: str | None = None,
        active: bool | None = None,
    ) -> dict:
        """Set a GoTracker window's cloud-only appointment-type override."""
        if any(value is not None for value in (days, start_time, end_time, operatory_id, active)):
            raise ValueError(
                "GoTracker working windows are PMS-owned; only appointment-type overrides can be updated."
            )
        if appointment_type_ids is None:
            raise ValueError("appointment_type_ids is required for a GoTracker override.")

        raw = await self._client.request(
            "PATCH",
            f"/api/scheduling/working_hours/{mappers.strip(availability_id)}",
            json={"appointment_type_ids": _strip_ids(appointment_type_ids)},
        )
        return _to_working_window(_data_object(raw, fallback_id=mappers.strip(availability_id)))

    async def clear_availability_override(self, availability_id: str) -> dict:
        """Remove a cloud override and expose Tracker's standing type links again."""
        raw = await self._client.request(
            "DELETE",
            f"/api/scheduling/working_hours/{mappers.strip(availability_id)}/override",
        )
        return _to_working_window(_data_object(raw, fallback_id=mappers.strip(availability_id)))

    # ── Slots ────────────────────────────────────────────────────────────

    async def get_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | list[str] | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
        tz_offset: str | None = None,
    ) -> list[UniversalSlot]:
        result = await self.find_available_slots(
            start_date=start_date,
            days=days,
            provider_id=provider_id,
            appointment_type_id=appointment_type_id,
            operatory_ids=operatory_ids,
            tz_offset=tz_offset,
        )
        return result.slots

    async def find_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | list[str] | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
        tz_offset: str | None = None,
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
        if tz_offset:
            params["tz_offset"] = tz_offset

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
        start_date: str | None = None,
        end_date: str | None = None,
        contact_id: str | None = None,
        from_date: str | None = None,
        exclude_cancelled: bool = False,
        max_items: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        if contact_id:
            params["contactId"] = mappers.strip(contact_id)
        if from_date:
            params["from"] = from_date
        if exclude_cancelled:
            params["exclude_cancelled"] = "true"
        return await self._fetch_all(
            "GET",
            "/api/appointments/getAllAppointments",
            params=params,
            max_items=max_items,
        )

    async def get_appointment(self, appointment_id: str) -> dict[str, Any] | None:
        """Fetch one GoTracker appointment by id from the Synchronizer API."""
        raw_id = mappers.strip(appointment_id).removeprefix("gt-")
        try:
            raw = await self._client.request("GET", f"/api/appointments/{raw_id}")
        except GoTrackerAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict):
            return None

        appointment = data.get("appointment")
        if isinstance(appointment, dict):
            return appointment

        appointments = data.get("appointments")
        if isinstance(appointments, list):
            for item in appointments:
                if not isinstance(item, dict):
                    continue
                item_id = mappers.strip(
                    item.get("AppointmentId")
                    or item.get("appointment_id")
                    or item.get("id")
                )
                if item_id == raw_id:
                    return item
            return None

        item_id = mappers.strip(
            data.get("AppointmentId") or data.get("appointment_id") or data.get("id")
        )
        return data if item_id == raw_id else None

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
        return await self.set_appointment_confirmation(
            appointment_id,
            confirmed=True,
            preconfirmed=False,
            success_status="confirmed",
            success_message="Appointment confirmed successfully.",
        )

    async def preconfirm_appointment(self, appointment_id: str) -> BookingResult:
        return await self.set_appointment_confirmation(
            appointment_id,
            preconfirmed=True,
            success_status="preconfirmed",
            success_message="Appointment pre-confirmed successfully.",
        )

    async def set_appointment_confirmation(
        self,
        appointment_id: str,
        *,
        confirmed: bool | None = None,
        preconfirmed: bool | None = None,
        success_status: str = "updated",
        success_message: str = "Appointment confirmation status updated.",
    ) -> BookingResult:
        body: dict[str, Any] = {}
        if confirmed is not None:
            body["confirmed"] = confirmed
        if preconfirmed is not None:
            body["preconfirmed"] = preconfirmed
        if not body:
            return BookingResult(
                success=False,
                source="gotracker",
                status="error",
                error="At least one confirmation flag is required.",
            )
        return await self._set_appointment_status(
            appointment_id,
            body,
            success_status=success_status,
            success_message=success_message,
        )

    async def set_appointment_status_id(
        self,
        appointment_id: str,
        *,
        status_id: int,
        confirmed: bool | None = None,
        preconfirmed: bool | None = None,
    ) -> BookingResult:
        body: dict[str, Any] = {"status_id": status_id}
        if confirmed is not None:
            body["confirmed"] = confirmed
        if preconfirmed is not None:
            body["preconfirmed"] = preconfirmed
        return await self._set_appointment_status(
            appointment_id,
            body,
            success_status="status_updated",
            success_message="Appointment status updated successfully.",
        )

    async def update_appointment(
        self,
        appointment_id: str,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        duration_min: int | None = None,
        provider_id: str | None = None,
        operatory_id: str | None = None,
        patient_id: str | None = None,
        reason: str | None = None,
    ) -> BookingResult:
        body: dict[str, Any] = {}
        if start_time:
            body["start_time"] = start_time
        if end_time:
            body["end_time"] = end_time
        if duration_min is not None:
            body["duration_min"] = duration_min
        if provider_id:
            body["provider_id"] = mappers.strip(provider_id)
        if operatory_id:
            body["operatory_id"] = mappers.strip(operatory_id)
        if patient_id:
            body["patient_id"] = mappers.strip(patient_id)
        if reason:
            body["reason"] = reason
        if not body:
            return BookingResult(
                success=False,
                source="gotracker",
                status="error",
                error="At least one appointment update field is required.",
            )

        try:
            await self._client.request(
                "PATCH",
                f"/api/appointments/{mappers.strip(appointment_id)}",
                json=body,
            )
            return BookingResult(
                success=True,
                source="gotracker",
                status="appointment_updated",
                message="Appointment updated successfully.",
            )
        except GoTrackerAPIError as exc:
            return BookingResult(
                success=False,
                source="gotracker",
                status="error",
                error=str(exc),
            )

    async def reschedule_appointment(
        self, old_appointment_id: str, new_booking: BookingRequest
    ) -> BookingResult:
        return await self.update_appointment(
            old_appointment_id,
            start_time=_wall_clock_datetime(new_booking.slot_start),
            duration_min=new_booking.duration_min,
            provider_id=new_booking.provider_id,
            operatory_id=new_booking.operatory_id,
            patient_id=new_booking.patient_id,
            reason=new_booking.note,
        )

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

    def _local_today(self) -> str:
        timezone_name = getattr(self._location, "timezone", None) or "UTC"
        try:
            return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError:
            return datetime.now(ZoneInfo("UTC")).date().isoformat()


def _is_non_attending_appointment(appointment: dict[str, Any]) -> bool:
    if bool(
        _first(appointment, "Cancelled", "cancelled", "IsCancelled", "is_cancelled")
    ):
        return True
    status_id = _first(appointment, "StatusId", "status_id")
    try:
        return is_non_attending_status(int(status_id))
    except (TypeError, ValueError):
        return False


def _strip_ids(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [stripped for value in values if (stripped := mappers.strip(value))]


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return None


def _data_object(raw: dict[str, Any], *, fallback_id: str | None = None) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if fallback_id is not None and data.get("id") is None:
        data = {**data, "id": fallback_id}
    return data


def _wall_clock_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.replace(tzinfo=None).isoformat(timespec="minutes")


def _list_data(raw: dict[str, Any], *, nested_key: str) -> list[dict[str, Any]]:
    data = raw.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get(nested_key), list):
        return [item for item in data[nested_key] if isinstance(item, dict)]
    if isinstance(raw.get(nested_key), list):
        return [item for item in raw[nested_key] if isinstance(item, dict)]
    return []


def _to_working_window(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the Synchronizer's working-window shape for setup routes."""
    window_id = _first(raw, "working_window_id", "id")
    return {
        "id": window_id,
        "provider_id": _first(raw, "ProviderId", "provider_id"),
        "operatory_id": _first(raw, "OperatoryId", "operatory_id"),
        "begin_time": _first(raw, "StartTime", "start_time"),
        "end_time": _first(raw, "EndTime", "end_time"),
        "specific_date": _first(raw, "WorkDate", "work_date"),
        "appointment_type_ids": _first(raw, "appointment_type_ids") or [],
        "active": True,
        "synced": _first(raw, "Source", "source") == "synced",
        "types_overridden": bool(_first(raw, "types_overridden")),
    }
