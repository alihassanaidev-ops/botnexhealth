"""NexHealth PMS adapter.

Wraps the existing NexHealthClient and translates all responses
into universal models. All NexHealth-specific complexity (descriptors,
availability linking, subdomain/location_id) lives here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.app.api.helpers import fetch_all_pages, handle_nexhealth_request
from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking
from src.app.pms.models import (
    BookingRequest,
    BookingResult,
    PatientCreateRequest,
    SetupStep,
    UniversalAppointmentType,
    UniversalLocation,
    UniversalOperatory,
    UniversalPatient,
    UniversalProvider,
    UniversalSlot,
)
from src.app.pms.nexhealth import mappers

if TYPE_CHECKING:
    from src.app.models.tenant import Tenant
    from src.app.nexhealth.client import NexHealthClient

logger = logging.getLogger(__name__)

PREFIX = "nh"


def _strip(prefixed_id: str) -> str:
    """Remove 'nh-' prefix to get raw NexHealth ID."""
    return prefixed_id.removeprefix(f"{PREFIX}-")


class NexHealthAdapter(PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking):
    source = "nexhealth"

    def __init__(self, client: NexHealthClient, tenant: Tenant) -> None:
        self._client = client
        self._tenant = tenant
        self._subdomain = tenant.nexhealth_subdomain
        self._location_id = tenant.nexhealth_location_id

    @classmethod
    async def create(cls, tenant: Tenant) -> NexHealthAdapter:
        """Factory: build a tenant-specific NexHealth client and wrap it."""
        from src.app.config import Settings, settings as global_settings
        from src.app.nexhealth.client import NexHealthClient

        tenant_settings = Settings(
            nexhealth_api_key=tenant.nexhealth_api_key,
            nexhealth_subdomain=tenant.nexhealth_subdomain or global_settings.nexhealth_subdomain,
            nexhealth_location_id=tenant.nexhealth_location_id or global_settings.nexhealth_location_id,
        )
        client = NexHealthClient(config=tenant_settings)
        await client.__aenter__()
        return cls(client, tenant)

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)

    # ── helpers ──────────────────────────────────────────────────────────

    def _default_params(self) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if self._subdomain:
            p["subdomain"] = self._subdomain
        if self._location_id:
            p["location_id"] = self._location_id
        return p

    # ── Patients ─────────────────────────────────────────────────────────

    async def search_patients(self, query: str, **kwargs: Any) -> list[UniversalPatient]:
        params = self._default_params()
        # Determine which field to search
        if kwargs.get("email"):
            params["email"] = kwargs["email"]
        elif kwargs.get("phone_number"):
            params["phone_number"] = kwargs["phone_number"]
        elif kwargs.get("date_of_birth"):
            params["date_of_birth"] = kwargs["date_of_birth"]
        else:
            params["name"] = query

        params.setdefault("page", 1)
        params.setdefault("per_page", 10)
        if kwargs.get("include"):
            params["include[]"] = kwargs["include"]

        raw = await handle_nexhealth_request(self._client, "GET", "/patients", params=params)
        patients = raw.get("data", {}).get("patients", [])
        return [mappers.to_patient(p) for p in patients]

    async def create_patient(self, req: PatientCreateRequest) -> dict[str, Any]:
        from src.app.api.models import (
            CreatePatientBio,
            CreatePatientData,
            CreatePatientProvider,
            CreatePatientRequest,
        )

        body = CreatePatientRequest(
            provider=CreatePatientProvider(provider_id=_strip(req.provider_id)),
            patient=CreatePatientData(
                first_name=req.first_name,
                last_name=req.last_name,
                email=req.email,
                bio=CreatePatientBio(
                    date_of_birth=req.date_of_birth,
                    phone_number=req.phone,
                    gender=req.gender,
                ),
            ),
        )

        params = self._default_params()
        raw = await handle_nexhealth_request(
            self._client, "POST", "/patients", params=params, json=body.model_dump()
        )
        user = raw.get("data", {}).get("user", {})
        return {
            "success": raw.get("code") is not False,
            "patient_id": f"{PREFIX}-{user.get('id')}" if user.get("id") else None,
            "message": f"Patient {user.get('first_name')} created successfully." if user.get("id") else raw.get("error", "Failed"),
        }

    # ── Appointment Types ────────────────────────────────────────────────

    async def list_appointment_types(self) -> list[UniversalAppointmentType]:
        params = self._default_params()
        params["include[]"] = ["descriptors"]
        raw = await handle_nexhealth_request(self._client, "GET", "/appointment_types", params=params)
        data = raw.get("data", [])
        return [mappers.to_appointment_type(at) for at in data]

    # ── Providers ────────────────────────────────────────────────────────

    async def list_providers(self) -> list[UniversalProvider]:
        params = self._default_params()

        async def fetch(page: int, per_page: int) -> dict[str, Any]:
            p = {**params, "page": page, "per_page": per_page, "include[]": ["availabilities", "appointment_types"]}
            return await handle_nexhealth_request(self._client, "GET", "/providers", params=p)

        all_raw = await fetch_all_pages(fetch, per_page=50, max_items=200)
        return [mappers.to_provider(p) for p in all_raw]

    # ── Operatories ──────────────────────────────────────────────────────

    async def list_operatories(self) -> list[UniversalOperatory]:
        params = {**self._default_params(), "page": 1, "per_page": 50}
        raw = await handle_nexhealth_request(self._client, "GET", "/operatories", params=params)
        data = raw.get("data", [])
        return [mappers.to_operatory(op) for op in data]

    # ── Slots ────────────────────────────────────────────────────────────

    async def get_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
    ) -> list[UniversalSlot]:
        params: dict[str, Any] = {
            "start_date": start_date,
            "days": days,
        }
        if self._subdomain:
            params["subdomain"] = self._subdomain
        if self._location_id:
            params["lids[]"] = [self._location_id]

        if provider_id:
            params["pids[]"] = [_strip(provider_id)]
        else:
            # Auto-fetch all providers for the location
            providers = await self.list_providers()
            params["pids[]"] = [_strip(p.id) for p in providers]

        if appointment_type_id:
            params["appointment_type_id"] = _strip(appointment_type_id)
        if operatory_ids:
            params["operatory_ids[]"] = [_strip(oid) for oid in operatory_ids]

        raw = await handle_nexhealth_request(self._client, "GET", "/appointment_slots", params=params)
        slots = raw.get("data", [])
        return [mappers.to_slot(s, appointment_type_id) for s in slots]

    # ── Booking ──────────────────────────────────────────────────────────

    async def book_appointment(self, req: BookingRequest) -> BookingResult:
        from src.app.api.models import CreateAppointmentBody, CreateAppointmentRequest

        body = CreateAppointmentBody(
            patient_id=_strip(req.patient_id),
            provider_id=_strip(req.provider_id),
            start_time=req.slot_start,
            end_time=req.slot_end,
            operatory_id=_strip(req.operatory_id) if req.operatory_id else None,
            appointment_type_id=_strip(req.appointment_type_id) if req.appointment_type_id else None,
            descriptor_ids=[_strip(d) for d in req.descriptor_ids] if req.descriptor_ids else None,
            note=req.note,
        )
        request_body = CreateAppointmentRequest(appt=body)

        params = self._default_params()
        params["notify_patient"] = True

        try:
            raw = await handle_nexhealth_request(
                self._client, "POST", "/appointments", params=params, json=request_body.model_dump()
            )
            if raw.get("code") is False or raw.get("error"):
                return BookingResult(
                    success=False,
                    source="nexhealth",
                    status="error",
                    error=raw.get("error") or raw.get("description") or "Unknown error",
                )
            return mappers.to_booking_result(raw, success=True)
        except Exception as e:
            return BookingResult(success=False, source="nexhealth", status="error", error=str(e))

    async def cancel_appointment(self, appointment_id: str) -> BookingResult:
        from src.app.api.models import CancelAppointmentBody, CancelAppointmentRequest

        body = CancelAppointmentRequest(appt=CancelAppointmentBody(cancelled=True))
        params = {"subdomain": self._subdomain} if self._subdomain else {}

        try:
            raw = await handle_nexhealth_request(
                self._client, "PATCH", f"/appointments/{_strip(appointment_id)}", params=params, json=body.model_dump()
            )
            if raw.get("code") is False:
                return BookingResult(success=False, source="nexhealth", status="error", error=raw.get("error", "Failed"))
            return BookingResult(success=True, source="nexhealth", status="cancelled", message="Appointment cancelled successfully.")
        except Exception as e:
            return BookingResult(success=False, source="nexhealth", status="error", error=str(e))

    async def reschedule_appointment(self, old_appointment_id: str, new_booking: BookingRequest) -> BookingResult:
        cancel_result = await self.cancel_appointment(old_appointment_id)
        if not cancel_result.success and "already cancelled" not in (cancel_result.error or "").lower():
            return BookingResult(
                success=False, source="nexhealth", status="error",
                error=f"Failed to cancel old appointment: {cancel_result.error}",
            )
        book_result = await self.book_appointment(new_booking)
        if book_result.success:
            book_result.message = "Rescheduled successfully (old cancelled, new booked)."
        return book_result

    # ── Locations ────────────────────────────────────────────────────────

    async def list_locations(self) -> list[UniversalLocation]:
        params: dict[str, Any] = {"page": 1, "per_page": 25}
        raw = await handle_nexhealth_request(self._client, "GET", "/institutions", params=params)
        data = raw.get("data", [])

        locations: list[UniversalLocation] = []
        for inst in data if isinstance(data, list) else []:
            subdomain = inst.get("subdomain")
            for loc in inst.get("locations", []):
                locations.append(mappers.to_location(loc, subdomain=subdomain))
        return locations

    async def get_location(self, location_id: str) -> UniversalLocation | None:
        try:
            raw = await handle_nexhealth_request(self._client, "GET", f"/locations/{_strip(location_id)}")
            loc = raw.get("data", {})
            return mappers.to_location(loc, subdomain=self._subdomain) if loc else None
        except Exception:
            return None

    # ── Setup ────────────────────────────────────────────────────────────

    async def get_setup_steps(self) -> list[SetupStep]:
        return [
            SetupStep(id="select_types", label="Select appointment types", description="Choose which appointment types to offer"),
            SetupStep(id="set_durations", label="Set durations", description="Set how long each appointment type takes"),
            SetupStep(id="link_operatories", label="Assign operatories", description="Link rooms/chairs to appointment types"),
            SetupStep(id="set_schedules", label="Set provider schedules", description="Configure provider availability by day"),
        ]

    # ── NexHealth-specific setup (optional capabilities) ─────────────────

    async def list_pms_descriptors(self) -> list[dict]:
        params = self._default_params()
        raw = await handle_nexhealth_request(self._client, "GET", "/appointment_descriptors", params=params)
        return raw.get("data", [])

    async def create_appointment_type(
        self, name: str, duration_minutes: int, descriptor_ids: list[str]
    ) -> UniversalAppointmentType:
        params = self._default_params()
        body = {
            "name": name,
            "minutes": duration_minutes,
            "appointment_descriptor_ids": [_strip(d) for d in descriptor_ids],
        }
        raw = await handle_nexhealth_request(self._client, "POST", "/appointment_types", params=params, json=body)
        return mappers.to_appointment_type(raw.get("data", {}))

    async def link_availability(
        self,
        provider_id: str,
        appointment_type_id: str,
        operatory_id: str,
        days: list[str],
        start_time: str,
        end_time: str,
    ) -> dict:
        params = self._default_params()
        body = {
            "provider_id": _strip(provider_id),
            "appointment_type_id": _strip(appointment_type_id),
            "operatory_id": _strip(operatory_id),
            "days": days,
            "begin_time": start_time,
            "end_time": end_time,
        }
        return await handle_nexhealth_request(self._client, "POST", "/availabilities", params=params, json=body)

    async def list_availabilities(self, **kwargs: Any) -> list[dict]:
        params = {**self._default_params(), **kwargs}
        raw = await handle_nexhealth_request(self._client, "GET", "/availabilities", params=params)
        return raw.get("data", [])
