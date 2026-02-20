"""Map NexHealth API responses to universal models."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.app.pms.models import (
    BookingResult,
    UniversalAppointmentType,
    UniversalLocation,
    UniversalOperatory,
    UniversalPatient,
    UniversalProvider,
    UniversalSlot,
)

PREFIX = "nh"


def _pid(raw_id: Any) -> str:
    return f"{PREFIX}-{raw_id}"


def to_patient(raw: dict) -> UniversalPatient:
    bio = raw.get("bio") or {}
    upcoming = raw.get("upcoming_appts") or []
    last_visited = raw.get("last_visited_appointment")
    procedures = raw.get("procedures") or []
    insurance_coverages = raw.get("insurance_coverages") or []

    extra: dict[str, Any] = {}
    if upcoming:
        extra["upcoming_appointments"] = [
            {
                "id": a.get("id"),
                "provider_id": a.get("provider_id"),
                "provider_name": a.get("provider_name"),
                "start_time": a.get("start_time"),
                "end_time": a.get("end_time"),
                "location_id": a.get("location_id"),
                "confirmed": a.get("confirmed"),
            }
            for a in upcoming[:3]
        ]
    if last_visited:
        extra["last_visit"] = {
            "id": last_visited.get("id"),
            "provider_id": last_visited.get("provider_id"),
            "provider_name": last_visited.get("provider_name"),
            "start_time": last_visited.get("start_time"),
            "end_time": last_visited.get("end_time"),
            "location_id": last_visited.get("location_id"),
            "confirmed": last_visited.get("confirmed"),
        }
    if procedures:
        extra["recent_procedures"] = [
            {
                "id": pr.get("id"),
                "code": pr.get("code"),
                "name": pr.get("name"),
                "status": pr.get("status"),
                "date": pr.get("start_date"),
            }
            for pr in procedures[:5]
        ]
    if insurance_coverages:
        extra["insurance_coverages"] = [
            {
                "id": ic.get("id"),
                "insurance_name": (ic.get("plan") or {}).get("name"),
                "group_number": (ic.get("plan") or {}).get("group_num"),
                "member_id": ic.get("subscriber_num"),
                "relation": ic.get("subscription_relation"),
                "priority": ic.get("priority"),
                "effective_date": ic.get("effective_date"),
                "expiration_date": ic.get("expiration_date"),
                "employer": (ic.get("plan") or {}).get("employer_name"),
            }
            for ic in insurance_coverages
        ]

    return UniversalPatient(
        id=_pid(raw.get("id")),
        source="nexhealth",
        first_name=raw.get("first_name", ""),
        last_name=raw.get("last_name", ""),
        email=raw.get("email"),
        phone=raw.get("phone_number") or bio.get("phone_number"),
        date_of_birth=raw.get("date_of_birth") or bio.get("date_of_birth"),
        extra=extra,
    )


def to_provider(raw: dict) -> UniversalProvider:
    appointment_types: list[dict] = []
    operatory_ids: list[str] = []
    today = date.today().isoformat()
    for avail in raw.get("availabilities") or []:
        # Skip inactive availability windows
        if avail.get("active") is False:
            continue
        # Skip one-off windows whose specific date has already passed
        specific_date = avail.get("specific_date")
        if specific_date and specific_date < today:
            continue
        op_id = avail.get("operatory_id")
        if op_id and _pid(op_id) not in operatory_ids:
            operatory_ids.append(_pid(op_id))
        for apt in avail.get("appointment_types") or []:
            apt_id = apt.get("id")
            if apt_id and not any(a.get("id") == _pid(apt_id) for a in appointment_types):
                appointment_types.append({
                    "id": _pid(apt_id),
                    "name": apt.get("name"),
                    "minutes": apt.get("minutes"),
                    "bookable_online": apt.get("bookable_online"),
                })

    return UniversalProvider(
        id=_pid(raw.get("id")),
        source="nexhealth",
        name=raw.get("name"),
        first_name=raw.get("first_name"),
        last_name=raw.get("last_name"),
        specialty=raw.get("nexhealth_specialty"),
        appointment_types=appointment_types,
        operatory_ids=operatory_ids,
    )


def to_appointment_type(raw: dict) -> UniversalAppointmentType:
    descriptors = raw.get("descriptors") or raw.get("appointment_descriptors") or []
    descriptor_ids = [str(d.get("id")) for d in descriptors if d.get("id")]
    return UniversalAppointmentType(
        id=_pid(raw.get("id")),
        source="nexhealth",
        name=raw.get("name", ""),
        duration_minutes=raw.get("minutes") or raw.get("duration"),
        source_id=str(raw.get("id")),
        source_metadata={
            "nh_appt_type_id": raw.get("id"),
            "descriptor_ids": descriptor_ids,
        },
    )


def to_operatory(raw: dict) -> UniversalOperatory:
    return UniversalOperatory(
        id=_pid(raw.get("id")),
        source="nexhealth",
        name=raw.get("name", ""),
        is_active=raw.get("active", True),
    )


def to_slot(raw: dict, appt_type_id: str | None = None) -> UniversalSlot:
    # NexHealth slots use "time" for start; provider_id may be on parent group as "_pid"
    provider_id = raw.get("provider_id") or raw.get("_pid")
    location_id = raw.get("location_id") or raw.get("_lid")
    return UniversalSlot(
        start=raw.get("time") or raw.get("start_time", ""),
        end=raw.get("end_time", ""),
        provider_id=_pid(provider_id) if provider_id else "",
        provider_name=raw.get("provider_name", ""),
        operatory_id=_pid(raw.get("operatory_id")) if raw.get("operatory_id") else None,
        operatory_name=raw.get("operatory_name"),
        appointment_type_id=appt_type_id,
        location_id=_pid(location_id) if location_id else None,
    )


def to_location(raw: dict, subdomain: str | None = None) -> UniversalLocation:
    return UniversalLocation(
        id=_pid(raw.get("id")),
        source="nexhealth",
        name=raw.get("name", ""),
        subdomain=subdomain,
        address=raw.get("address") or raw.get("street_address"),
        city=raw.get("city"),
        phone=raw.get("phone"),
        timezone=raw.get("timezone"),
        hours=raw.get("hours"),
    )


def to_booking_result(raw: dict, success: bool = True) -> BookingResult:
    appt = raw.get("data", {}).get("appt", {})
    if not appt:
        appt = raw
    return BookingResult(
        success=success,
        id=_pid(appt.get("id")) if appt.get("id") else None,
        source="nexhealth",
        status="confirmed" if success else "error",
        start=appt.get("start_time"),
        end=appt.get("end_time"),
        patient_id=_pid(appt.get("patient_id")) if appt.get("patient_id") else None,
        provider_id=_pid(appt.get("provider_id")) if appt.get("provider_id") else None,
        message="Appointment booked successfully." if success else "",
    )
