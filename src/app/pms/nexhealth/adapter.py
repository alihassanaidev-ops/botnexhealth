"""NexHealth PMS adapter.

Wraps the existing NexHealthClient and translates all responses
into universal models. All NexHealth-specific complexity (descriptors,
availability linking, subdomain/location_id) lives here.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from src.app.api.helpers import fetch_all_pages, handle_nexhealth_request
from src.app.pms.base import (
    PMSAdapter,
    SupportsAppointmentConfirmation,
    SupportsAppointmentTypeCreation,
    SupportsAvailabilityLinking,
)
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
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation
    from src.app.nexhealth.client import NexHealthClient

logger = logging.getLogger(__name__)

PREFIX = "nh"


def _strip(prefixed_id: str) -> str:
    """Remove 'nh-' prefix to get raw NexHealth ID."""
    return prefixed_id.removeprefix(f"{PREFIX}-")


def _normalize_phone_for_nexhealth(phone: str | None) -> str | None:
    """Normalize phone to a 10-digit form NexHealth will accept and find,
    tolerating the common ways a user or an LLM might shape a number.

    Two NexHealth quirks combine here:

      1. ``POST /patients`` truncates ``phone_number`` to the first 10
         characters at storage time but does exact-string match on
         ``GET /patients?phone_number=``. Pre-truncating on both paths
         keeps create and lookup pointing at the same key.

      2. ``POST /patients`` validates against NANP area-code rules
         ("User phone number is invalid" with code=false). NANP area
         codes must start with 2-9 — never 1 or 0 — so a US number
         passed in ``+1NNNXXXXXXX`` form would, after a naive first-10
         truncation, become ``1NNNXXXXXX`` whose "area code" is ``1NN``
         and gets rejected.

    The function accepts any of these inputs and produces the same
    canonical 10-digit value:

      * ``+1 (505) 482-1234`` / ``1-505-482-1234`` / ``15054821234``
        → ``5054821234``  (NANP 10-digit, country code stripped)
      * ``5054821234`` / ``(505) 482-1234``
        → ``5054821234``  (already 10-digit NANP, kept as-is)
      * ``03485619645`` / ``(0348) 561-9645``
        → ``0348561964``  (PK 11-digit, leading 0, first-10 to match
        NexHealth's storage truncation)
      * Other shapes / international country codes other than ``1``
        → first 10 digits, same matching strategy

    Strict E.164 (``+`` prefix) is parsed identically to the same digits
    without the ``+``; non-digit separators are stripped before any
    decision.
    """
    if not phone:
        return None
    digits = "".join(c for c in str(phone) if c.isdigit())
    if not digits:
        return None

    # US/CA E.164: 11 digits with leading country-code "1". The "1"
    # is NOT part of the area code; strip it so the next 10 are the
    # NANP-valid form.
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]

    # Already 10-digit NANP (area code 2-9). Keep as-is — passes
    # NexHealth's NANP validator without further surgery.
    if len(digits) == 10 and digits[0] in "23456789":
        return digits

    # Everything else (PK ``0XXXXXXXXXX``, other-country E.164,
    # padded/odd shapes): first 10 chars, matching NexHealth's
    # silent storage truncation so that lookup and create agree.
    return digits[:10]


class NexHealthAdapter(
    PMSAdapter,
    SupportsAppointmentConfirmation,
    SupportsAppointmentTypeCreation,
    SupportsAvailabilityLinking,
):
    source = "nexhealth"

    def __init__(
        self,
        client: NexHealthClient,
        institution: Institution,
        *,
        subdomain: str | None = None,
        location_id: str | None = None,
        owns_client: bool = False,
    ) -> None:
        self._client = client
        self._institution = institution
        self._subdomain = subdomain
        self._location_id = location_id
        self._owns_client = owns_client

    @classmethod
    async def create(cls, institution: Institution, location: InstitutionLocation) -> NexHealthAdapter:
        """Build a NexHealth adapter scoped to an institution + location.

        The platform shares a single NexHealth account, so the API key comes
        from global settings. Per-clinic isolation is provided exclusively by
        ``location.nexhealth_subdomain`` and ``location.nexhealth_location_id``;
        we fail closed if either is missing to prevent a misconfigured clinic
        from silently routing to whichever subdomain happens to be in the
        global env.
        """
        from src.app.config import settings as global_settings
        from src.app.dependencies import get_nexhealth_client_dependency

        api_key = global_settings.nexhealth_api_key
        if not api_key:
            raise RuntimeError("NEXHEALTH_API_KEY is not configured")

        subdomain = location.nexhealth_subdomain
        location_id = location.nexhealth_location_id
        if not subdomain or not location_id:
            raise ValueError(
                f"Location {location.slug} is missing nexhealth_subdomain or "
                "nexhealth_location_id; cannot create PMS adapter"
            )

        # Use the process-level NexHealth client so HTTP connection pooling
        # and token caching survive across requests. Creating a fresh
        # AsyncClient per adapter leaks sockets if a caller misses close()
        # and collapses under concurrent Retell/function traffic.
        client = await get_nexhealth_client_dependency()
        return cls(
            client,
            institution,
            subdomain=subdomain,
            location_id=location_id,
            owns_client=False,
        )

    async def close(self) -> None:
        if self._owns_client and self._client:
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
            params["phone_number"] = _normalize_phone_for_nexhealth(kwargs["phone_number"])
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

    async def get_patient(self, patient_id: str) -> UniversalPatient | None:
        """Fetch a single patient by NexHealth ID.

        Returns ``None`` if the patient cannot be found. Used to read the
        email address NexHealth has on file (collected at intake) rather than
        trusting a value transcribed by the voice agent during a call.
        """
        params = self._default_params()
        try:
            raw = await handle_nexhealth_request(
                self._client, "GET", f"/patients/{_strip(patient_id)}", params=params
            )
        except Exception:
            return None
        data = raw.get("data") or {}
        patient = data.get("user") or data.get("patient") or data
        if not isinstance(patient, dict) or not patient.get("id"):
            return None
        return mappers.to_patient(patient)

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
                    phone_number=_normalize_phone_for_nexhealth(req.phone),
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

    # ── Appointment Queries ─────────────────────────────────────────────

    async def has_provider_appointments_on_date(
        self, provider_id: str, date_str: str
    ) -> bool:
        """Check NexHealth for any booked appointments for a provider on a date."""
        try:
            # Scan pages until we find at least one active appointment.
            # We keep this bounded for latency/cost safety.
            per_page = 50
            for page in range(1, 11):
                params = self._default_params()
                params["start_date"] = date_str
                params["end_date"] = date_str
                params["provider_id"] = _strip(provider_id)
                params["page"] = page
                params["per_page"] = per_page

                raw = await handle_nexhealth_request(
                    self._client, "GET", "/appointments", params=params
                )
                data = raw.get("data", [])
                if not isinstance(data, list):
                    logger.warning(
                        f"Unexpected appointments payload type while checking provider schedule: {type(data)}"
                    )
                    return True

                for appt in data:
                    cancelled = bool(appt.get("cancelled", False) or appt.get("canceled", False))
                    if not cancelled:
                        return True

                # No more pages to scan.
                if len(data) < per_page:
                    break

            return False
        except Exception as e:
            logger.warning(f"Failed to check provider appointments: {e}")
            return True  # safe fallback — don't hide slots

    async def get_appointment(self, appointment_id: str) -> dict[str, Any] | None:
        """Fetch a single appointment's current record from NexHealth.

        Returns the raw appointment dict (carrying ``cancelled``/``start_time``)
        or ``None`` if it cannot be read. Used by the dispatch-time revalidator
        (Plan 09) so a cancelled or rescheduled appointment is not messaged.
        Goes through ``handle_nexhealth_request`` so the shared API key's
        rate-limit/pacing wrapper still applies.
        """
        params = self._default_params()
        try:
            raw = await handle_nexhealth_request(
                self._client, "GET", f"/appointments/{_strip(appointment_id)}", params=params
            )
        except Exception:
            return None
        data = raw.get("data")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return data

    async def list_appointments(
        self,
        *,
        start_date: str,
        end_date: str,
        max_items: int = 1000,
    ) -> list[dict[str, Any]]:
        """List raw NexHealth appointments for this location/date window.

        Used by Plan 09 backfill and reconciliation. This intentionally returns
        raw appointment dictionaries because the projection only needs a small
        scheduling subset and NexHealth payloads vary by PMS. The call still
        goes through ``handle_nexhealth_request`` + ``fetch_all_pages`` so the
        shared client, auth, and rate limiter remain authoritative.
        """
        params = self._default_params()

        async def fetch(page: int, per_page: int) -> dict[str, Any]:
            p = {
                **params,
                "start_date": start_date,
                "end_date": end_date,
                "page": page,
                "per_page": per_page,
            }
            return await handle_nexhealth_request(
                self._client, "GET", "/appointments", params=p
            )

        return await fetch_all_pages(fetch, per_page=50, max_items=max_items)

    async def list_patient_recalls(self, *, max_items: int = 500) -> list[dict[str, Any]]:
        """List patient recall records for this location from NexHealth.

        NexHealth exposes recall queues (``GET /recalls``) scoped by subdomain +
        location (capability "View patient recalls"). Each record carries a
        ``patient_id`` and a ``due_date``; the recall scanner derives "overdue"
        from the due date. Paged via the shared ``fetch_all_pages`` helper so the
        shared API key's rate limiter/pacing wrapper still governs the pull.
        """
        params = self._default_params()

        async def fetch(page: int, per_page: int) -> dict[str, Any]:
            p = {**params, "page": page, "per_page": per_page}
            return await handle_nexhealth_request(self._client, "GET", "/recalls", params=p)

        return await fetch_all_pages(fetch, per_page=50, max_items=max_items)

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
        provider_id: str | list[str] | None = None,
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
            if isinstance(provider_id, list):
                params["pids[]"] = [_strip(pid) for pid in provider_id]
            else:
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
        # NexHealth returns nested: data = [{lid, pid, slots: [{time, end_time, ...}]}]
        result: list[UniversalSlot] = []
        for group in raw.get("data", []):
            group_pid = group.get("pid")
            group_lid = group.get("lid")
            for slot in group.get("slots", []):
                slot["_pid"] = group_pid
                slot["_lid"] = group_lid
                result.append(mappers.to_slot(slot, appointment_type_id))
        return result

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
        params = self._default_params()

        try:
            raw = await handle_nexhealth_request(
                self._client, "PATCH", f"/appointments/{_strip(appointment_id)}", params=params, json=body.model_dump()
            )
            if raw.get("code") is False:
                return BookingResult(success=False, source="nexhealth", status="error", error=raw.get("error", "Failed"))
            return BookingResult(success=True, source="nexhealth", status="cancelled", message="Appointment cancelled successfully.")
        except Exception as e:
            return BookingResult(success=False, source="nexhealth", status="error", error=str(e))

    async def confirm_appointment(self, appointment_id: str) -> BookingResult:
        from src.app.api.models import ConfirmAppointmentBody, ConfirmAppointmentRequest

        body = ConfirmAppointmentRequest(appt=ConfirmAppointmentBody(confirmed=True))
        params = self._default_params()

        try:
            raw = await handle_nexhealth_request(
                self._client,
                "PATCH",
                f"/appointments/{_strip(appointment_id)}",
                params=params,
                json=body.model_dump(),
            )
            if raw.get("code") is False:
                return BookingResult(
                    success=False,
                    source="nexhealth",
                    status="error",
                    error=raw.get("error", "Failed"),
                )
            return BookingResult(
                success=True,
                source="nexhealth",
                status="confirmed",
                message="Appointment confirmed successfully.",
            )
        except Exception as e:
            return BookingResult(success=False, source="nexhealth", status="error", error=str(e))

    async def reschedule_appointment(self, old_appointment_id: str, new_booking: BookingRequest) -> BookingResult:
        # Book the new slot first so the patient never loses coverage if the new
        # booking fails. Only cancel the old appointment after the new one is
        # confirmed.
        book_result = await self.book_appointment(new_booking)
        if not book_result.success:
            return book_result

        cancel_result = await self.cancel_appointment(old_appointment_id)
        if not cancel_result.success and "already cancelled" not in (cancel_result.error or "").lower():
            book_result.message = (
                "Rescheduled (new booked) but failed to cancel old appointment: "
                f"{cancel_result.error}. Please cancel manually."
            )
        else:
            book_result.message = "Rescheduled successfully (new booked, old cancelled)."
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
        raw = await handle_nexhealth_request(
            self._client, "GET", f"/locations/{self._location_id}/appointment_descriptors", params=params
        )
        return raw.get("data", [])

    async def create_appointment_type(
        self, name: str, duration_minutes: int, descriptor_ids: list[str]
    ) -> UniversalAppointmentType:
        params = self._default_params()
        # NexHealth REST convention: write endpoints expect the resource
        # wrapped under the singular resource name. A flat body returns
        # ``{"error":["Missing parameter appointment_type"]}`` (HTTP 400).
        # Matches the wrap pattern already used by ``update_appointment_type``.
        body = {
            "appointment_type": {
                "name": name,
                "minutes": duration_minutes,
                "appointment_descriptor_ids": [_strip(d) for d in descriptor_ids],
            }
        }
        raw = await handle_nexhealth_request(self._client, "POST", "/appointment_types", params=params, json=body)
        return mappers.to_appointment_type(raw.get("data", {}))

    async def update_appointment_type(
        self,
        appointment_type_id: str,
        name: str | None = None,
        duration_minutes: int | None = None,
        descriptor_ids: list[str] | None = None,
    ) -> UniversalAppointmentType:
        params = self._default_params()
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if duration_minutes is not None:
            payload["minutes"] = duration_minutes
        if descriptor_ids is not None:
            def _to_int(value: str) -> int | str:
                stripped = _strip(value)
                try:
                    return int(stripped)
                except (TypeError, ValueError):
                    return stripped

            payload["emr_appt_descriptor_ids"] = [_to_int(d) for d in descriptor_ids]

        raw = await handle_nexhealth_request(
            self._client,
            "PATCH",
            f"/appointment_types/{_strip(appointment_type_id)}",
            params=params,
            json={"appointment_type": payload},
        )
        return mappers.to_appointment_type(raw.get("data", {}))

    async def link_availability(
        self,
        provider_id: str,
        appointment_type_ids: list[str],
        operatory_id: str,
        days: list[str],
        start_time: str,
        end_time: str,
    ) -> dict:
        params = self._default_params()
        body = {
            "provider_id": _strip(provider_id),
            "appointment_type_ids": [_strip(aid) for aid in appointment_type_ids],
            "operatory_id": _strip(operatory_id),
            "days": days,
            "begin_time": start_time,
            "end_time": end_time,
        }
        return await handle_nexhealth_request(
            self._client, "POST", "/availabilities", params=params, json={"availability": body}
        )

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
        params = self._default_params()
        body: dict[str, Any] = {}
        if appointment_type_ids is not None:
            body["appointment_type_ids"] = [int(_strip(aid)) for aid in appointment_type_ids]
        if days is not None:
            body["days"] = days
        if start_time is not None:
            body["begin_time"] = start_time
        if end_time is not None:
            body["end_time"] = end_time
        if operatory_id is not None:
            body["operatory_id"] = int(_strip(operatory_id))
        if active is not None:
            body["active"] = active

        raw = await handle_nexhealth_request(
            self._client, "PATCH", f"/availabilities/{_strip(availability_id)}",
            params=params, json={"availability": body},
        )
        return raw.get("data", {})

    async def list_availabilities(self, **kwargs: Any) -> list[dict]:
        provider_id = kwargs.pop("provider_id", None)
        ignore_past_dates = bool(kwargs.get("ignore_past_dates", False))

        params = {
            **self._default_params(),
            "per_page": 100,
            "ignore_past_dates": True,
            **kwargs,  # caller can still override
        }
        if provider_id:
            params["provider_id"] = _strip(provider_id)
        if "include[]" not in params:
            params["include[]"] = ["appointment_types"]

        raw = await handle_nexhealth_request(self._client, "GET", "/availabilities", params=params)
        direct_items = raw.get("data", [])
        if not isinstance(direct_items, list):
            direct_items = []

        # NexHealth's /availabilities endpoint can return 200 with no rows for
        # normal PMS-synced provider schedules. Those same work windows are
        # exposed on /providers when availabilities are included, so merge that
        # embedded source as the display/read path for setup.
        provider_items = await self._list_provider_embedded_availabilities(
            provider_id=_strip(provider_id) if provider_id else None,
            ignore_past_dates=ignore_past_dates,
        )

        merged: dict[str, dict] = {}
        for item in [*direct_items, *provider_items]:
            item_id = item.get("id")
            key = str(item_id) if item_id is not None else repr(sorted(item.items()))
            merged[key] = item
        return list(merged.values())

    async def _list_provider_embedded_availabilities(
        self,
        *,
        provider_id: str | None = None,
        ignore_past_dates: bool = True,
    ) -> list[dict]:
        params = self._default_params()

        async def fetch(page: int, per_page: int) -> dict[str, Any]:
            p = {
                **params,
                "page": page,
                "per_page": per_page,
                "include[]": ["availabilities", "appointment_types"],
            }
            return await handle_nexhealth_request(self._client, "GET", "/providers", params=p)

        providers = await fetch_all_pages(fetch, per_page=50, max_items=200)
        today = date.today().isoformat()
        items: list[dict] = []

        for provider in providers:
            raw_provider_id = provider.get("id")
            if provider_id and str(raw_provider_id) != str(provider_id):
                continue

            provider_name = (
                provider.get("name")
                or " ".join(
                    part for part in [provider.get("first_name"), provider.get("last_name")]
                    if part
                )
                or None
            )
            for availability in provider.get("availabilities") or []:
                if availability.get("active") is False:
                    continue
                specific_date = availability.get("specific_date")
                if ignore_past_dates and specific_date and specific_date < today:
                    continue

                item = dict(availability)
                item.setdefault("provider_id", raw_provider_id)
                item.setdefault("provider_name", provider_name)
                items.append(item)

        return items
