"""Sikka PMS adapter.

Wraps the existing SikkaClient and translates all responses
into universal models. Sikka requires office_id + secret_key
(stored as encrypted tenant credentials) for every API call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.app.pms.base import PMSAdapter
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
from src.app.pms.sikka import mappers

if TYPE_CHECKING:
    from src.app.models.tenant import Tenant
    from src.app.sikka.client import SikkaClient

logger = logging.getLogger(__name__)

PREFIX = "sk"


def _strip(prefixed_id: str) -> str:
    """Remove 'sk-' prefix to get raw Sikka ID."""
    return prefixed_id.removeprefix(f"{PREFIX}-")


class SikkaAdapter(PMSAdapter):
    source = "sikka"

    def __init__(self, client: SikkaClient, tenant: Tenant, location: TenantLocation | None = None) -> None:
        self._client = client
        self._tenant = tenant
        self._location = location
        self._office_id = location.sikka_office_id if location else ""

    @classmethod
    async def create(cls, tenant: Tenant, location: TenantLocation | None = None) -> SikkaAdapter:
        """Factory: build a tenant-specific Sikka client and wrap it."""
        from src.app.config import SikkaConfig, settings as global_settings
        from src.app.sikka.client import SikkaClient

        # Sikka uses global app credentials + per-practice office_id/secret
        sikka_config = SikkaConfig(global_settings)

        # Override with tenant-specific credentials if available
        if tenant.sikka_app_id and tenant.sikka_app_secret:
            class TenantSikkaConfig:
                @property
                def app_id(self) -> str:
                    return tenant.sikka_app_id or ""
                @property
                def app_secret(self) -> str:
                    return tenant.sikka_app_secret or ""
                @property
                def base_url(self) -> str:
                    return global_settings.sikka_base_url
                @property
                def api_version(self) -> str:
                    return global_settings.sikka_api_version

            sikka_config = TenantSikkaConfig()

        client = SikkaClient(
            config=sikka_config,
            office_id=location.sikka_office_id if location else None,
        )
        await client.__aenter__()
        return cls(client, tenant, location)

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)

    # ── Patients ─────────────────────────────────────────────────────────

    async def search_patients(self, query: str, **kwargs: Any) -> list[UniversalPatient]:
        params: dict[str, Any] = {"page": 1, "per_page": 10}

        if kwargs.get("email"):
            params["email"] = kwargs["email"]
        elif kwargs.get("phone_number"):
            params["cell"] = kwargs["phone_number"]
        else:
            params["search"] = query

        raw = await self._client.get("/patients", params=params)
        patients = raw.get("data", [])
        if isinstance(patients, dict):
            patients = patients.get("patients", [])
        return [mappers.to_patient(p) for p in patients]

    async def create_patient(self, req: PatientCreateRequest) -> dict[str, Any]:
        body = {
            "firstname": req.first_name,
            "lastname": req.last_name,
            "email": req.email,
            "cell": req.phone,
            "birthdate": req.date_of_birth,
        }
        try:
            raw = await self._client.post("/patients", json=body)
            patient = raw.get("data", {})
            pid = patient.get("patient_id") or patient.get("id")
            return {
                "success": True,
                "patient_id": f"{PREFIX}-{pid}" if pid else None,
                "message": f"Patient {req.first_name} created successfully.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Appointment Types ────────────────────────────────────────────────

    async def list_appointment_types(self) -> list[UniversalAppointmentType]:
        try:
            raw = await self._client.get("/appointment_types")
            data = raw.get("data", [])
            return [mappers.to_appointment_type(at) for at in data]
        except Exception as e:
            logger.warning(f"Sikka list_appointment_types failed: {e}")
            return []

    # ── Providers ────────────────────────────────────────────────────────

    async def list_providers(self) -> list[UniversalProvider]:
        try:
            raw = await self._client.get("/providers")
            data = raw.get("data", [])
            return [mappers.to_provider(p) for p in data]
        except Exception as e:
            logger.warning(f"Sikka list_providers failed: {e}")
            return []

    # ── Operatories ──────────────────────────────────────────────────────

    async def list_operatories(self) -> list[UniversalOperatory]:
        try:
            raw = await self._client.get("/operatories")
            data = raw.get("data", [])
            return [mappers.to_operatory(op) for op in data]
        except Exception as e:
            logger.warning(f"Sikka list_operatories failed: {e}")
            return []

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
        if provider_id:
            params["provider_id"] = _strip(provider_id)
        if appointment_type_id:
            params["appointment_type_id"] = _strip(appointment_type_id)

        try:
            raw = await self._client.get("/appointments/openslots", params=params)
            slots = raw.get("data", [])
            return [mappers.to_slot(s, appointment_type_id) for s in slots]
        except Exception as e:
            logger.warning(f"Sikka get_available_slots failed: {e}")
            return []

    # ── Booking ──────────────────────────────────────────────────────────

    async def book_appointment(self, req: BookingRequest) -> BookingResult:
        body: dict[str, Any] = {
            "patient_id": _strip(req.patient_id),
            "provider_id": _strip(req.provider_id),
            "start_time": req.slot_start,
        }
        if req.slot_end:
            body["end_time"] = req.slot_end
        if req.operatory_id:
            body["operatory_id"] = _strip(req.operatory_id)
        if req.note:
            body["note"] = req.note

        try:
            raw = await self._client.post("/appointments", json=body)
            return mappers.to_booking_result(raw, success=True)
        except Exception as e:
            return BookingResult(success=False, source="sikka", status="error", error=str(e))

    async def cancel_appointment(self, appointment_id: str) -> BookingResult:
        try:
            await self._client.patch(f"/appointments/{_strip(appointment_id)}", json={"status": "cancelled"})
            return BookingResult(success=True, source="sikka", status="cancelled", message="Appointment cancelled.")
        except Exception as e:
            return BookingResult(success=False, source="sikka", status="error", error=str(e))

    async def reschedule_appointment(self, old_appointment_id: str, new_booking: BookingRequest) -> BookingResult:
        cancel_result = await self.cancel_appointment(old_appointment_id)
        if not cancel_result.success:
            return BookingResult(success=False, source="sikka", status="error", error=f"Cancel failed: {cancel_result.error}")
        book_result = await self.book_appointment(new_booking)
        if book_result.success:
            book_result.message = "Rescheduled successfully."
        return book_result

    # ── Locations ────────────────────────────────────────────────────────

    async def list_locations(self) -> list[UniversalLocation]:
        try:
            practices = await self._client.get_authorized_practices()
            return [mappers.to_location(p) for p in practices]
        except Exception as e:
            logger.warning(f"Sikka list_locations failed: {e}")
            return []

    async def get_location(self, location_id: str) -> UniversalLocation | None:
        locations = await self.list_locations()
        return next((l for l in locations if l.id == location_id), None)

    # ── Setup ────────────────────────────────────────────────────────────

    async def get_setup_steps(self) -> list[SetupStep]:
        return [
            SetupStep(id="authorize_practice", label="Authorize practice", description="Connect your practice via Sikka marketplace"),
            SetupStep(id="set_schedules", label="Set provider schedules", description="Configure provider availability"),
        ]
