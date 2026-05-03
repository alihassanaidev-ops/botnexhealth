"""Retell function handlers — PMS-agnostic via adapter pattern.

All handlers resolve the institution and location automatically from the
call context (agent_id → InstitutionLocation mapping). Since each Retell
agent maps 1:1 to a location, the agent never needs to specify
location_id — the backend routes automatically.

HIPAA Note: Only hashed identifiers appear in logs.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date, datetime
from itertools import groupby
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_location_transfer_number import (
    InstitutionLocationTransferNumber,
)
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.insurance_plan import InsurancePlan
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.base import PMSAdapter, SupportsAvailabilityLinking
from src.app.pms.factory import get_adapter_for_institution, get_adapter_for_institution_location
from src.app.pms.models import BookingRequest, PatientCreateRequest
from src.app.retell.functions import get_institution_from_call_context, register_function
from src.app.retell.security import hash_for_logging
from src.app.services.audit_decorator import audit
from src.app.services.slot_filter import (
    apply_buffer,
    apply_time_restriction,
    get_local_date_string,
    merge_buffer_minutes,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Context Resolution
# ============================================================================


@dataclass
class ResolvedContext:
    """Resolved institution, location, and PMS adapter from call context."""

    institution: Institution
    location: InstitutionLocation | None
    adapter: PMSAdapter


async def _resolve_context() -> ResolvedContext:
    """Resolve PMS adapter and location from current Retell call context.

    Returns a ResolvedContext containing the institution, location (if mapped),
    and the scoped PMS adapter. Since each Retell agent is mapped 1:1 to an
    InstitutionLocation, the location is automatically resolved — the agent
    does not need to call list_locations or pass location_id.

    Raises:
        ValueError: If no institution can be resolved from the agent_id.
    """
    institution, location = await get_institution_from_call_context()
    if not institution:
        raise ValueError("No institution resolved from call context. Check agent_id mapping.")

    if location:
        adapter = await get_adapter_for_institution_location(institution, location)
    else:
        adapter = await get_adapter_for_institution(institution)

    return ResolvedContext(institution=institution, location=location, adapter=adapter)


# ============================================================================
# Privacy Helpers (HIPAA-safe masking for patient data)
# ============================================================================


def _mask_email(value: str | None) -> str | None:
    """Mask email for safe identity hints in basic lookup responses."""
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}" if local else None
    return f"{local[0]}***{local[-1]}@{domain}"


def _mask_phone(value: str | None) -> str | None:
    """Mask phone for safe identity hints in basic lookup responses."""
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 4:
        return None
    return f"***-***-{digits[-4:]}"


async def _validate_appointment_type_for_provider(
    ctx: ResolvedContext,
    provider_id: str | None,
    appointment_type_id: str | None,
) -> str | None:
    """Ensure appointment_type_id is linked to provider availability if supported."""
    if not appointment_type_id:
        return None
    if not provider_id:
        return "provider_id is required when appointment_type_id is provided."
    if not isinstance(ctx.adapter, SupportsAvailabilityLinking):
        return None

    raw_provider_id = provider_id.removeprefix("nh-")
    raw_appt_id = appointment_type_id.removeprefix("nh-")
    try:
        availabilities = await ctx.adapter.list_availabilities(provider_id=raw_provider_id)
    except Exception as e:
        logger.error("Failed to validate appointment type: %s", e)
        return "Unable to validate appointment type for this provider."

    allowed_ids = {
        str(at.get("id"))
        for avail in availabilities
        for at in (avail.get("appointment_types") or [])
        if at.get("id") is not None
    }
    if raw_appt_id not in allowed_ids:
        return "Appointment type is not available for this provider."
    return None


def _to_basic_patient_payload(patient: Any) -> dict[str, Any]:
    """Return minimum necessary patient identity payload."""
    return {
        "id": patient.id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "has_email": bool(patient.email),
        "has_phone": bool(patient.phone),
        "email_hint": _mask_email(patient.email),
        "phone_hint": _mask_phone(patient.phone),
    }


def _to_full_patient_payload(patient: Any) -> dict[str, Any]:
    """Return richer payload for explicitly requested full detail lookups."""
    return {
        "id": patient.id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "email": patient.email,
        "phone_number": patient.phone,
        "date_of_birth": patient.date_of_birth,
        **patient.extra,
    }


# ============================================================================
# Location Functions (auto-routed)
# ============================================================================


@register_function("list_locations")
@audit(AuditAction.READ_LOCATIONS, resource="auto_resolved_location")
async def list_locations(args: dict[str, Any]) -> dict[str, Any]:
    """Return the auto-resolved location for this Retell agent.

    Since each agent maps 1:1 to an InstitutionLocation, this returns
    exactly one location — no PMS API call needed.
    """
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    if ctx.location:
        return {
            "count": 1,
            "locations": [
                {
                    "id": ctx.location.nexhealth_location_id or ctx.location.id,
                    "name": ctx.location.name,
                    "slug": ctx.location.slug,
                    "address": ctx.location.address,
                    "city": ctx.location.city,
                    "state": ctx.location.state,
                    "phone": ctx.location.phone,
                    "timezone": ctx.location.timezone,
                }
            ],
            "message": f"Your location is {ctx.location.name}.",
        }

    # Fallback: institution-only (no location mapped), fetch from PMS
    try:
        locations = await ctx.adapter.list_locations()
        return {
            "count": len(locations),
            "locations": [loc.model_dump() for loc in locations],
            "message": f"Found {len(locations)} location(s).",
        }
    except Exception as e:
        logger.error(f"Failed to list locations: {e}")
        return {"error": f"Failed to list locations: {str(e)}"}


@register_function("get_location_details")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda args: f"location:{args.get('location_id', 'auto')}",
)
async def get_location_details(args: dict[str, Any]) -> dict[str, Any]:
    """Get location details for FAQs (hours, address, etc).

    location_id is optional — defaults to the auto-resolved location.
    """
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    location_id = args.get("location_id")

    # Auto-resolve: use the mapped location if no explicit ID
    if not location_id and ctx.location:
        return {
            "practice_name": ctx.location.name,
            "location": {
                "id": ctx.location.nexhealth_location_id or ctx.location.id,
                "name": ctx.location.name,
                "slug": ctx.location.slug,
                "address": ctx.location.address,
                "city": ctx.location.city,
                "state": ctx.location.state,
                "phone": ctx.location.phone,
                "timezone": ctx.location.timezone,
            },
        }

    # Explicit location_id or no mapped location — fetch from PMS
    target_id = location_id or (ctx.location.nexhealth_location_id if ctx.location else None)
    if not target_id:
        return {"error": "No location could be resolved."}

    try:
        loc = await ctx.adapter.get_location(target_id)
        if not loc:
            return {"error": "Location not found."}
        return {
            "practice_name": loc.name,
            "location": loc.model_dump(),
        }
    except Exception as e:
        logger.error(f"Failed to get location details: {e}")
        return {"error": f"Failed to retrieve location details: {str(e)}"}


# ============================================================================
# Patient Functions
# ============================================================================


@register_function("lookup_patient")
@audit(
    AuditAction.SEARCH_PATIENTS,
    resource=lambda args: "patient_search:by_" + ",".join(
        k for k, v in [("name", args.get("name")), ("phone", args.get("phone_number")),
                        ("email", args.get("email")), ("dob", args.get("date_of_birth"))] if v
    ),
)
async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
    """Lookup a patient by name, email, phone, or date of birth."""
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"message": str(e)}

    detail_level = str(args.get("detail_level", "basic")).lower()
    if detail_level not in {"basic", "full"}:
        detail_level = "basic"

    query = args.get("name") or args.get("email") or args.get("phone_number") or ""
    if not query and not args.get("date_of_birth"):
        return {"message": "Please provide at least one search criterion (name, email, phone, or DOB)."}

    include = None
    if detail_level == "full":
        include = ["upcoming_appts", "last_visited_appointment", "procedures", "insurance_coverages"]

    try:
        patients = await ctx.adapter.search_patients(
            query,
            email=args.get("email"),
            phone_number=args.get("phone_number"),
            date_of_birth=args.get("date_of_birth"),
            include=include,
        )
    except Exception as e:
        logger.error(f"Patient lookup failed: {e}")
        return {"message": "I had trouble accessing the patient records. Please try again."}

    if not patients:
        return {
            "match_status": "none",
            "message": "No patients found matching the criteria.",
        }

    match_count = len(patients)
    needs_disambiguation = match_count > 1
    effective_detail_level = "basic" if needs_disambiguation else detail_level

    if effective_detail_level == "full":
        simplified = [_to_full_patient_payload(p) for p in patients[:10]]
        if ctx.location:
            tz_str = (ctx.location.timezone or "UTC").strip()
            try:
                tz = ZoneInfo(tz_str)
            except ZoneInfoNotFoundError:
                logger.warning(
                    f"Invalid timezone for location {ctx.location.id}: {tz_str!r}. Falling back to UTC."
                )
                tz_str = "UTC"
                tz = ZoneInfo(tz_str)

            def _parse_iso(value: str) -> datetime | None:
                try:
                    raw = value.strip()
                    if raw.endswith("Z"):
                        raw = raw[:-1] + "+00:00"
                    return datetime.fromisoformat(raw)
                except Exception:
                    return None

            def _to_local_iso(value: str | None) -> str | None:
                if not value:
                    return None
                dt = _parse_iso(value)
                if not dt:
                    return None
                if dt.tzinfo is None:
                    # If timezone is missing, assume UTC to avoid shifting twice.
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                local_dt = dt.astimezone(tz).replace(microsecond=0)
                return local_dt.isoformat()

            for patient in simplified:
                patient["timezone"] = tz_str
                upcoming = patient.get("upcoming_appointments")
                if isinstance(upcoming, list):
                    for appt in upcoming:
                        if isinstance(appt, dict):
                            appt["start_time_local"] = _to_local_iso(appt.get("start_time"))
                            appt["end_time_local"] = _to_local_iso(appt.get("end_time"))
                last_visit = patient.get("last_visit")
                if isinstance(last_visit, dict):
                    last_visit["start_time_local"] = _to_local_iso(last_visit.get("start_time"))
                    last_visit["end_time_local"] = _to_local_iso(last_visit.get("end_time"))
    else:
        simplified = [_to_basic_patient_payload(p) for p in patients[:10]]

    return {
        "detail_level": effective_detail_level,
        "count": len(simplified),
        "patients": simplified,
        "match_status": "multiple" if needs_disambiguation else "single",
        "disambiguation_required": needs_disambiguation,
        "disambiguation_hints": ["date_of_birth", "email"] if needs_disambiguation else [],
        "message": (
            "Multiple patients matched. Please ask for date of birth or email to confirm."
            if needs_disambiguation
            else f"Found {len(simplified)} patient(s)."
        ),
    }


@register_function("create_patient")
@audit(
    AuditAction.CREATE_PATIENT,
    resource=lambda args: "new_patient:created",
)
async def create_patient(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new patient."""
    required = ["first_name", "last_name", "email", "phone_number", "date_of_birth", "provider_id"]
    for field in required:
        if not args.get(field):
            return {"error": f"{field} is required."}

    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        return await ctx.adapter.create_patient(
            PatientCreateRequest(
                first_name=args["first_name"],
                last_name=args["last_name"],
                email=args["email"],
                phone=args["phone_number"],
                date_of_birth=args["date_of_birth"],
                provider_id=args["provider_id"],
                gender=args.get("gender", "Female"),
            )
        )
    except Exception as e:
        logger.error(f"Failed to create patient: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# Available Slots
# ============================================================================


@register_function("find_appointment_slots")
@audit(
    AuditAction.READ_APPOINTMENT_SLOTS,
    resource=lambda args: f"slots:{args.get('start_date')}",
)
async def find_appointment_slots(args: dict[str, Any]) -> dict[str, Any]:
    """Find available appointment slots.

    Supports optional ``buffer_minutes`` — minimum lead-time from now.
    Slots starting before now + buffer are excluded.
    """
    start_date = args.get("start_date")
    if not start_date:
        return {"error": "start_date is required."}

    appt_type_id = args.get("appointment_type_id")
    if not appt_type_id:
        return {"error": "appointment_type_id is required."}

    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    raw_provider = args.get("provider_id")
    provider_ids: list[str] | None = None
    provider_id: str | None = None
    if raw_provider is not None:
        if isinstance(raw_provider, list):
            provider_ids = [str(pid) for pid in raw_provider if pid]
        else:
            provider_ids = [str(raw_provider)]
        if provider_ids:
            provider_id = provider_ids[0]
        else:
            provider_ids = None

    if appt_type_id and provider_id:
        validation_error = await _validate_appointment_type_for_provider(
            ctx, provider_id, appt_type_id
        )
        if validation_error:
            return {"error": validation_error}

    try:
        slots = await ctx.adapter.get_available_slots(
            start_date=start_date,
            days=args.get("days", 7),
            provider_id=provider_ids if provider_ids is not None else None,
            appointment_type_id=appt_type_id,
            operatory_ids=args.get("operatory_ids"),
        )

        # Apply provider-level filters (buffer + time restriction)
        try:
            buffer_minutes = max(0, int(args.get("buffer_minutes", 0)))
        except (TypeError, ValueError):
            return {"error": "buffer_minutes must be an integer >= 0."}

        normalized_provider_id = (
            str(provider_id).removeprefix("nh-")
            if provider_id
            else None
        )
        provider_source_id = (
            f"nh-{normalized_provider_id}" if normalized_provider_id else None
        )
        provider_cutoff = None

        if normalized_provider_id and ctx.location:
            async with get_db_session() as session:
                prov = (await session.execute(
                    select(
                        InstitutionProvider.buffer_minutes,
                        InstitutionProvider.same_day_cutoff_time,
                    ).where(
                        InstitutionProvider.source_id == provider_source_id,
                        InstitutionProvider.location_id == str(ctx.location.id),
                    )
                )).one_or_none()
                if prov:
                    provider_buffer = max(0, int(prov.buffer_minutes or 0))
                    buffer_minutes = merge_buffer_minutes(buffer_minutes, provider_buffer)
                    provider_cutoff = prov.same_day_cutoff_time

        if buffer_minutes > 0:
            slots = apply_buffer(slots, buffer_minutes)

        # Apply same-day cutoff time restriction
        if provider_cutoff and normalized_provider_id and ctx.location:
            tz_str = ctx.location.timezone or "UTC"
            today_str = get_local_date_string(tz_str)
            has_appts = await ctx.adapter.has_provider_appointments_on_date(
                normalized_provider_id, today_str
            )
            slots = apply_time_restriction(
                slots=slots,
                cutoff_time=provider_cutoff,
                has_appointments_today=has_appts,
                timezone=tz_str,
            )

        # Shuffle provider group order so the AI doesn't always favour the first provider
        keyfunc = lambda s: s.provider_id
        grouped = {pid: list(group) for pid, group in groupby(sorted(slots, key=keyfunc), key=keyfunc)}
        provider_ids = list(grouped.keys())
        random.shuffle(provider_ids)
        slots = [slot for pid in provider_ids for slot in grouped[pid]]

        return {
            "slots_count": len(slots),
            "slots": [s.model_dump() for s in slots],
            "message": f"Found {len(slots)} available slot(s).",
        }
    except Exception as e:
        logger.error(f"Failed to find slots: {e}")
        return {"error": f"Failed to find slots: {str(e)}"}


# ============================================================================
# Appointments
# ============================================================================


@register_function("book_appointment")
@audit(
    AuditAction.BOOK_APPOINTMENT,
    resource=lambda args: f"appt_for:{args.get('patient_id')}",
)
async def book_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """Book a new appointment."""
    required = ["patient_id", "provider_id", "start_time"]
    for field in required:
        if not args.get(field):
            return {"error": f"{field} is required."}
    if not args.get("appointment_type_id"):
        return {"error": "appointment_type_id is required."}

    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    validation_error = await _validate_appointment_type_for_provider(
        ctx, args.get("provider_id"), args.get("appointment_type_id")
    )
    if validation_error:
        return {"success": False, "error": validation_error}

    try:
        result = await ctx.adapter.book_appointment(
            BookingRequest(
                patient_id=args["patient_id"],
                provider_id=args["provider_id"],
                slot_start=args["start_time"],
                slot_end=args.get("end_time"),
                operatory_id=args.get("operatory_id"),
                appointment_type_id=args.get("appointment_type_id"),
                descriptor_ids=args.get("descriptor_ids", []),
                note=args.get("note"),
            )
        )
        return result.model_dump()
    except Exception as e:
        logger.error(f"Failed to book appointment: {e}")
        return {"success": False, "error": str(e)}


@register_function("cancel_appointment")
@audit(
    AuditAction.CANCEL_APPOINTMENT,
    resource=lambda args: f"appointment:{args.get('appointment_id')}",
)
async def cancel_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel an existing appointment."""
    appointment_id = args.get("appointment_id")
    if not appointment_id:
        return {"error": "appointment_id is required."}

    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        result = await ctx.adapter.cancel_appointment(appointment_id)
        return result.model_dump()
    except Exception as e:
        logger.error(f"Failed to cancel appointment: {e}")
        return {"success": False, "error": str(e)}


@register_function("reschedule_appointment")
@audit(
    AuditAction.RESCHEDULE_APPOINTMENT,
    resource=lambda args: f"reschedule:old={args.get('old_appointment_id')}",
)
async def reschedule_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """Reschedule an appointment (cancel old + book new)."""
    old_id = args.get("old_appointment_id")
    if not old_id:
        return {"error": "old_appointment_id is required."}

    required = ["patient_id", "provider_id", "start_time"]
    for field in required:
        if not args.get(field):
            return {"error": f"{field} is required for the new booking."}
    if not args.get("appointment_type_id"):
        return {"error": "appointment_type_id is required for the new booking."}

    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    validation_error = await _validate_appointment_type_for_provider(
        ctx, args.get("provider_id"), args.get("appointment_type_id")
    )
    if validation_error:
        return {"success": False, "error": validation_error}

    try:
        result = await ctx.adapter.reschedule_appointment(
            old_id,
            BookingRequest(
                patient_id=args["patient_id"],
                provider_id=args["provider_id"],
                slot_start=args["start_time"],
                slot_end=args.get("end_time"),
                operatory_id=args.get("operatory_id"),
                appointment_type_id=args.get("appointment_type_id"),
                descriptor_ids=args.get("descriptor_ids", []),
                note=args.get("note"),
            ),
        )
        return result.model_dump()
    except Exception as e:
        logger.error(f"Failed to reschedule: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# Info / FAQ Functions
# ============================================================================


@register_function("list_appointment_types")
@audit(
    AuditAction.READ_APPOINTMENT_TYPES,
    resource=lambda args: "appointment_types",
)
async def list_appointment_types(args: dict[str, Any]) -> dict[str, Any]:
    """List appointment types for a practice."""
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    try:
        types = await ctx.adapter.list_appointment_types()
        simplified = [
            {
                "id": t.id,
                "name": t.name,
                "minutes": t.duration_minutes,
                "descriptor_ids": t.source_metadata.get("descriptor_ids", []),
            }
            for t in types
        ]
        return {
            "count": len(simplified),
            "appointment_types": simplified,
            "message": f"Found {len(simplified)} appointment types.",
        }
    except Exception as e:
        logger.error(f"Failed to list appointment types: {e}")
        return {"error": str(e)}


@register_function("list_providers")
@audit(
    AuditAction.READ_PROVIDERS,
    resource=lambda args: "providers",
)
async def list_providers(args: dict[str, Any]) -> dict[str, Any]:
    """List providers at the practice, optionally filtered by patient age.

    If ``date_of_birth`` (YYYY-MM-DD) is supplied, only providers whose
    configured age range covers the patient's age are returned.  Providers
    with no age-range configured are always included.
    """
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    try:
        providers = await ctx.adapter.list_providers()

        # ── Age-group filtering ──────────────────────────────────────
        patient_dob = args.get("date_of_birth")
        patient_age: int | None = None
        if patient_dob and ctx.location:
            try:
                dob = date.fromisoformat(patient_dob)
                today = date.today()
                patient_age = (
                    today.year - dob.year
                    - ((today.month, today.day) < (dob.month, dob.day))
                )
            except (ValueError, TypeError):
                logger.warning(f"Invalid date_of_birth format: {hash_for_logging(patient_dob)}")

        if patient_age is not None and ctx.location:
            # Look up age-group rules from local cache
            age_rules: dict[str, tuple[int | None, int | None]] = {}
            async with get_db_session() as session:
                rows = (
                    await session.execute(
                        select(
                            InstitutionProvider.source_id,
                            InstitutionProvider.min_age,
                            InstitutionProvider.max_age,
                        ).where(
                            InstitutionProvider.location_id == str(ctx.location.id),
                            InstitutionProvider.is_active == True,
                        )
                    )
                ).all()
                for row in rows:
                    age_rules[row.source_id] = (row.min_age, row.max_age)

            filtered = []
            for p in providers:
                rule = age_rules.get(f"nh-{p.id}") or age_rules.get(str(p.id))
                if rule is None:
                    # No local cache entry — include by default
                    filtered.append(p)
                    continue
                p_min, p_max = rule
                if p_min is not None and patient_age < p_min:
                    continue
                if p_max is not None and patient_age > p_max:
                    continue
                filtered.append(p)
            providers = filtered

        simplified = [
            {
                "id": p.id,
                "name": p.name,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "specialty": p.specialty,
                "appointment_types": p.appointment_types,
                "operatory_ids": p.operatory_ids,
            }
            for p in providers
        ]
        return {
            "count": len(simplified),
            "providers": simplified,
            "message": f"Found {len(simplified)} provider(s).",
        }
    except Exception as e:
        logger.error(f"Failed to list providers: {e}")
        return {"error": f"Failed to list providers: {str(e)}"}


@register_function("list_insurance_plans")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda args: "insurance_plans",
)
async def list_insurance_plans_handler(args: dict[str, Any]) -> dict[str, Any]:
    """List accepted insurance plans for this location."""
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    if not ctx.location:
        return {"error": "No location resolved for this agent."}

    try:
        async with get_db_session() as session:
            plans = (
                await session.execute(
                    select(InsurancePlan).where(
                        InsurancePlan.location_id == str(ctx.location.id),
                        InsurancePlan.institution_id == str(ctx.institution.id),
                        InsurancePlan.is_active == True,
                    ).order_by(InsurancePlan.name)
                )
            ).scalars().all()

            simplified = [
                {"name": p.name, "description": p.description or ""}
                for p in plans
            ]

            if not simplified:
                return {
                    "count": 0,
                    "insurance_plans": [],
                    "message": "This location has not listed any accepted insurance plans yet. Please ask the caller to contact the office for insurance verification.",
                }

            return {
                "count": len(simplified),
                "insurance_plans": simplified,
                "message": f"We accept {len(simplified)} insurance plan(s): {', '.join(p['name'] for p in simplified)}.",
            }
    except Exception as e:
        logger.error(f"Failed to list insurance plans: {e}")
        return {"error": f"Failed to retrieve insurance plans: {str(e)}"}


@register_function("list_transfer_numbers")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda args: "transfer_numbers",
)
async def list_transfer_numbers(args: dict[str, Any]) -> dict[str, Any]:
    """List transfer numbers for this location."""
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    if not ctx.location:
        return {"error": "No location resolved for this agent."}

    try:
        async with get_db_session() as session:
            timezone = (ctx.location.timezone or "UTC").strip()
            try:
                tz = ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                logger.warning(f"Invalid timezone for location {ctx.location.id}: {timezone!r}. Falling back to UTC.")
                timezone = "UTC"
                tz = ZoneInfo(timezone)

            local_now = datetime.now(tz)
            local_day = local_now.weekday()
            now_time = local_now.time()

            rows = (
                await session.execute(
                    select(InstitutionLocationTransferNumber)
                    .where(
                        InstitutionLocationTransferNumber.location_id == str(ctx.location.id),
                        InstitutionLocationTransferNumber.institution_id == str(ctx.institution.id),
                    )
                    .order_by(
                        InstitutionLocationTransferNumber.department,
                        InstitutionLocationTransferNumber.phone_number,
                    )
                )
            ).scalars().all()

            simplified = [
                {"phone_number": r.phone_number, "department": r.department}
                for r in rows
            ]

            hours_rows = (
                await session.execute(
                    select(LocationOperatingHours).where(
                        LocationOperatingHours.location_id == str(ctx.location.id)
                    )
                )
            ).scalars().all()

            breaks_rows = (
                await session.execute(
                    select(LocationBreak).where(
                        LocationBreak.location_id == str(ctx.location.id)
                    )
                )
            ).scalars().all()

            opens_at = None
            closes_at = None
            is_open = False
            on_lunch_break = False

            if hours_rows:
                hours_by_day = {h.day_of_week: h for h in hours_rows}
                day_hours = hours_by_day.get(local_day)

                if day_hours:
                    opens_at = day_hours.open_time.strftime("%H:%M") if day_hours.open_time else None
                    closes_at = day_hours.close_time.strftime("%H:%M") if day_hours.close_time else None

                    if day_hours.is_open:
                        if day_hours.open_time and day_hours.close_time:
                            is_open = day_hours.open_time <= now_time < day_hours.close_time
                        else:
                            is_open = True

                        if is_open and breaks_rows:
                            breaks_by_day: dict[int | None, list[LocationBreak]] = {}
                            for brk in breaks_rows:
                                breaks_by_day.setdefault(brk.day_of_week, []).append(brk)

                            applicable_breaks = breaks_by_day.get(local_day, []) + breaks_by_day.get(None, [])
                            for brk in applicable_breaks:
                                if brk.start_time <= now_time < brk.end_time:
                                    on_lunch_break = True
                                    is_open = False
                                    break

            if not simplified:
                return {
                    "count": 0,
                    "transfer_numbers": [],
                    "is_open": is_open,
                    "opens_at": opens_at,
                    "closes_at": closes_at,
                    "on_lunch_break": on_lunch_break,
                    "message": "No transfer numbers are configured for this location.",
                }

            return {
                "count": len(simplified),
                "transfer_numbers": simplified,
                "is_open": is_open,
                "opens_at": opens_at,
                "closes_at": closes_at,
                "on_lunch_break": on_lunch_break,
                "message": f"Found {len(simplified)} transfer number(s).",
            }
    except Exception as e:
        logger.error(f"Failed to list transfer numbers: {e}")
        return {"error": f"Failed to retrieve transfer numbers: {str(e)}"}


@register_function("list_operatories")
async def list_operatories(args: dict[str, Any]) -> dict[str, Any]:
    """List operatories (chairs/rooms) at the practice."""
    try:
        ctx = await _resolve_context()
    except ValueError as e:
        return {"error": str(e)}

    try:
        ops = await ctx.adapter.list_operatories()
        simplified = [
            {"id": op.id, "name": op.name, "active": op.is_active}
            for op in ops
        ]
        return {
            "count": len(simplified),
            "operatories": simplified,
            "message": f"Found {len(simplified)} operatories.",
        }
    except Exception as e:
        logger.error(f"Failed to list operatories: {e}")
        return {"error": f"Failed to list operatories: {str(e)}"}
