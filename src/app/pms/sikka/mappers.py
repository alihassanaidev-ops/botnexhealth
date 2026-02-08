"""Map Sikka API responses to universal models."""

from __future__ import annotations

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

PREFIX = "sk"


def _pid(raw_id: Any) -> str:
    return f"{PREFIX}-{raw_id}"


def to_patient(raw: dict) -> UniversalPatient:
    return UniversalPatient(
        id=_pid(raw.get("patient_id") or raw.get("id")),
        source="sikka",
        first_name=raw.get("firstname") or raw.get("first_name", ""),
        last_name=raw.get("lastname") or raw.get("last_name", ""),
        email=raw.get("email"),
        phone=raw.get("cell") or raw.get("phone"),
        date_of_birth=raw.get("birthdate") or raw.get("date_of_birth"),
    )


def to_provider(raw: dict) -> UniversalProvider:
    return UniversalProvider(
        id=_pid(raw.get("provider_id") or raw.get("id")),
        source="sikka",
        name=raw.get("name"),
        first_name=raw.get("firstname") or raw.get("first_name"),
        last_name=raw.get("lastname") or raw.get("last_name"),
        specialty=raw.get("specialty"),
    )


def to_appointment_type(raw: dict) -> UniversalAppointmentType:
    return UniversalAppointmentType(
        id=_pid(raw.get("id") or raw.get("procedure_code")),
        source="sikka",
        name=raw.get("name") or raw.get("description", ""),
        duration_minutes=raw.get("duration") or raw.get("minutes"),
        source_id=str(raw.get("id") or raw.get("procedure_code", "")),
        source_metadata={
            "sikka_procedure_code": raw.get("procedure_code"),
        },
    )


def to_operatory(raw: dict) -> UniversalOperatory:
    return UniversalOperatory(
        id=_pid(raw.get("operatory_id") or raw.get("id")),
        source="sikka",
        name=raw.get("name") or raw.get("operatory_name", ""),
        is_active=raw.get("active", True),
    )


def to_slot(raw: dict, appt_type_id: str | None = None) -> UniversalSlot:
    return UniversalSlot(
        start=raw.get("start_time") or raw.get("start", ""),
        end=raw.get("end_time") or raw.get("end", ""),
        provider_id=_pid(raw.get("provider_id")),
        provider_name=raw.get("provider_name", ""),
        operatory_id=_pid(raw.get("operatory_id")) if raw.get("operatory_id") else None,
        operatory_name=raw.get("operatory_name"),
        appointment_type_id=appt_type_id,
    )


def to_location(raw: dict) -> UniversalLocation:
    return UniversalLocation(
        id=_pid(raw.get("office_id") or raw.get("id")),
        source="sikka",
        name=raw.get("practice_name") or raw.get("name", ""),
        address=raw.get("address"),
        city=raw.get("city"),
        phone=raw.get("phone"),
    )


def to_booking_result(raw: dict, success: bool = True) -> BookingResult:
    appt = raw.get("appointment") or raw
    return BookingResult(
        success=success,
        id=_pid(appt.get("id") or appt.get("appointment_id")) if appt.get("id") or appt.get("appointment_id") else None,
        source="sikka",
        status="confirmed" if success else "error",
        start=appt.get("start_time") or appt.get("start"),
        end=appt.get("end_time") or appt.get("end"),
        patient_id=_pid(appt.get("patient_id")) if appt.get("patient_id") else None,
        provider_id=_pid(appt.get("provider_id")) if appt.get("provider_id") else None,
        message="Appointment booked successfully." if success else "",
    )
