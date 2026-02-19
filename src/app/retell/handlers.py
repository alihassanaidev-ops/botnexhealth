"""Retell function handlers — PMS-agnostic via adapter pattern.

All handlers resolve the tenant from call context, get the appropriate
PMS adapter, and call universal methods. No PMS-specific branching.
"""

from __future__ import annotations

import logging
from typing import Any

from src.app.models.audit_log import AuditAction, AuditActor
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_adapter_for_tenant, get_adapter_for_tenant_location
from src.app.pms.models import BookingRequest, PatientCreateRequest
from src.app.retell.functions import get_tenant_from_call_context, register_function
from src.app.services.audit_decorator import audit

logger = logging.getLogger(__name__)


async def _get_adapter() -> PMSAdapter:
    """Resolve PMS adapter from current Retell call context."""
    tenant, location = await get_tenant_from_call_context()
    if not tenant:
        raise ValueError("No tenant resolved from call context. Check agent_id mapping.")
    if location:
        return await get_adapter_for_tenant_location(tenant, location)
    return await get_adapter_for_tenant(tenant)


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
# Patient Functions
# ============================================================================


@register_function("lookup_patient")
@audit(
    AuditAction.SEARCH_PATIENTS,
    resource=lambda args: f"criteria:name={args.get('name')},phone={args.get('phone_number')},email={args.get('email')}",
)
async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
    """Lookup a patient by name, email, phone, or date of birth."""
    try:
        adapter = await _get_adapter()
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
        include = ["upcoming_appts", "last_visited_appointment", "procedures"]

    try:
        patients = await adapter.search_patients(
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
        return {"message": "No patients found matching the criteria."}

    if detail_level == "full":
        simplified = [_to_full_patient_payload(p) for p in patients[:10]]
    else:
        simplified = [_to_basic_patient_payload(p) for p in patients[:10]]

    return {
        "detail_level": detail_level,
        "count": len(simplified),
        "patients": simplified,
        "message": f"Found {len(simplified)} patient(s).",
    }


@register_function("create_patient")
@audit(
    AuditAction.CREATE_PATIENT,
    resource=lambda args: f"new_patient:{args.get('first_name')}_{args.get('last_name')}",
)
async def create_patient(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new patient."""
    required = ["first_name", "last_name", "email", "phone_number", "date_of_birth", "provider_id"]
    for field in required:
        if not args.get(field):
            return {"error": f"{field} is required."}

    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        return await adapter.create_patient(
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
    """Find available appointment slots."""
    start_date = args.get("start_date")
    if not start_date:
        return {"error": "start_date is required."}

    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        slots = await adapter.get_available_slots(
            start_date=start_date,
            days=args.get("days", 7),
            provider_id=args.get("provider_id"),
            appointment_type_id=args.get("appointment_type_id"),
            operatory_ids=args.get("operatory_ids"),
        )
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

    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        result = await adapter.book_appointment(
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
        adapter = await _get_adapter()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        result = await adapter.cancel_appointment(appointment_id)
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

    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        result = await adapter.reschedule_appointment(
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
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        types = await adapter.list_appointment_types()
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


@register_function("get_location_details")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda args: f"location:{args.get('location_id')}",
)
async def get_location_details(args: dict[str, Any]) -> dict[str, Any]:
    """Get location details for FAQs (hours, address, etc)."""
    location_id = args.get("location_id")
    if not location_id:
        return {"error": "location_id is required."}

    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        loc = await adapter.get_location(location_id)
        if not loc:
            return {"error": "Location not found."}
        return {
            "practice_name": loc.name,
            "location": loc.model_dump(),
        }
    except Exception as e:
        logger.error(f"Failed to get location details: {e}")
        return {"error": f"Failed to retrieve location details: {str(e)}"}


@register_function("list_locations")
@audit(AuditAction.READ_LOCATIONS, resource="all_locations")
async def list_locations(args: dict[str, Any]) -> dict[str, Any]:
    """List all available practice locations."""
    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        locations = await adapter.list_locations()
        simplified = [loc.model_dump() for loc in locations]
        return {
            "count": len(simplified),
            "locations": simplified,
            "message": f"Found {len(simplified)} location(s).",
        }
    except Exception as e:
        logger.error(f"Failed to list locations: {e}")
        return {"error": f"Failed to list locations: {str(e)}"}


@register_function("list_providers")
@audit(
    AuditAction.READ_PROVIDERS,
    resource=lambda args: "providers",
)
async def list_providers(args: dict[str, Any]) -> dict[str, Any]:
    """List all providers at the practice."""
    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        providers = await adapter.list_providers()
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


@register_function("list_operatories")
async def list_operatories(args: dict[str, Any]) -> dict[str, Any]:
    """List operatories (chairs/rooms) at the practice."""
    try:
        adapter = await _get_adapter()
    except ValueError as e:
        return {"error": str(e)}

    try:
        ops = await adapter.list_operatories()
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
