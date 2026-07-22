"""Map GoTracker Synchronizer responses to universal PMS models."""

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

PREFIX = "gt"


def pid(raw_id: Any) -> str:
    return f"{PREFIX}-{raw_id}"


def strip(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value)
    prefix = f"{PREFIX}-"
    return raw[len(prefix):] if raw.startswith(prefix) else raw


def to_patient(raw: dict[str, Any]) -> UniversalPatient:
    raw_id = _first(raw, "ContactId", "contact_id", "id", "patient_id")
    first_name = _first(raw, "FirstName", "first_name", "firstName") or ""
    last_name = _first(raw, "LastName", "last_name", "lastName") or ""
    return UniversalPatient(
        id=pid(raw_id),
        source="gotracker",
        first_name=str(first_name),
        last_name=str(last_name),
        email=_first(raw, "Email", "email"),
        phone=_first(raw, "Phone", "phone", "PhoneNumber", "phone_number", "CellPhone"),
        date_of_birth=_first(raw, "DateOfBirth", "DOB", "date_of_birth"),
        extra={"raw": _minimum_extra(raw)},
    )


def to_provider(raw: dict[str, Any]) -> UniversalProvider:
    raw_id = _first(raw, "ProviderId", "provider_id", "id")
    first_name = _first(raw, "FirstName", "first_name")
    last_name = _first(raw, "LastName", "last_name")
    name = _first(raw, "Name", "name")
    appointment_types = []
    for item in raw.get("appointment_types") or raw.get("AppointmentTypes") or []:
        item_id = _first(item, "id", "AppointmentTypeId", "appointment_type_id")
        if item_id is None:
            continue
        appointment_types.append(
            {
                "id": pid(item_id),
                "name": _first(item, "name", "Name"),
                "minutes": _first(item, "minutes", "Minutes", "duration_minutes"),
                "bookable_online": _first(item, "bookable_online", "BookableOnline"),
            }
        )

    operatory_ids = [
        pid(item)
        for item in raw.get("operatory_ids") or raw.get("OperatoryIds") or []
        if item is not None
    ]

    return UniversalProvider(
        id=pid(raw_id),
        source="gotracker",
        name=name,
        first_name=first_name,
        last_name=last_name,
        specialty=_first(raw, "Specialty", "specialty"),
        appointment_types=appointment_types,
        operatory_ids=operatory_ids,
    )


def to_appointment_type(raw: dict[str, Any]) -> UniversalAppointmentType:
    raw_id = _first(raw, "id", "AppointmentTypeId", "appointment_type_id")
    return UniversalAppointmentType(
        id=pid(raw_id),
        source="gotracker",
        name=str(_first(raw, "name", "Name") or ""),
        duration_minutes=_first(raw, "minutes", "Minutes", "duration_minutes"),
        source_id=str(raw_id),
        source_metadata={
            "gotracker_appointment_type_id": raw_id,
            "provider_ids": raw.get("provider_ids") or raw.get("ProviderIds") or [],
            "operatory_ids": raw.get("operatory_ids") or raw.get("OperatoryIds") or [],
        },
    )


def to_operatory(raw: dict[str, Any]) -> UniversalOperatory:
    raw_id = _first(raw, "OperatoryId", "operatory_id", "id")
    return UniversalOperatory(
        id=pid(raw_id),
        source="gotracker",
        name=str(_first(raw, "Name", "name") or ""),
        is_active=bool(_first(raw, "IsActive", "is_active", default=True)),
    )


def to_slot(
    raw: dict[str, Any],
    *,
    provider_id: Any | None = None,
    location_id: Any | None = None,
    appointment_type_id: str | None = None,
) -> UniversalSlot:
    raw_provider_id = _first(raw, "provider_id", "ProviderId", default=provider_id)
    raw_location_id = _first(raw, "lid", "LocationId", "location_id", default=location_id)
    raw_operatory_id = _first(raw, "operatory_id", "OperatoryId")
    return UniversalSlot(
        start=str(_first(raw, "time", "start_time", "StartTime") or ""),
        end=str(_first(raw, "end_time", "EndTime") or ""),
        provider_id=pid(raw_provider_id) if raw_provider_id is not None else "",
        provider_name=str(_first(raw, "provider_name", "ProviderName") or ""),
        operatory_id=pid(raw_operatory_id) if raw_operatory_id is not None else None,
        operatory_name=_first(raw, "operatory_name", "OperatoryName"),
        appointment_type_id=appointment_type_id,
        location_id=pid(raw_location_id) if raw_location_id is not None else None,
    )


def to_location(raw: dict[str, Any]) -> UniversalLocation:
    raw_id = _first(raw, "LocationId", "location_id", "id")
    return UniversalLocation(
        id=pid(raw_id),
        source="gotracker",
        name=str(_first(raw, "LocationName", "name", "Name") or ""),
        subdomain=None,
        address=_first(raw, "Address", "address"),
        city=_first(raw, "City", "city"),
        phone=_first(raw, "Phone", "phone"),
        timezone=_first(raw, "Timezone", "timezone"),
        hours=raw.get("hours"),
    )


def to_booking_result(raw: dict[str, Any], *, success: bool = True) -> BookingResult:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    appointment_id = _first(data, "appointment_id", "AppointmentId", "id")
    start = _first(data, "start_time", "StartTime")
    end = _first(data, "end_time", "EndTime")

    return BookingResult(
        success=success,
        id=pid(appointment_id) if appointment_id is not None else None,
        source="gotracker",
        status=str(_first(data, "status", "Status") or ("scheduled" if success else "error")),
        start=start,
        end=end,
        patient_id=_maybe_pid(data, "patient_id", "PatientId", "ContactId"),
        provider_id=_maybe_pid(data, "provider_id", "ProviderId"),
        appointment_type_id=_maybe_pid(data, "appointment_type_id", "AppointmentTypeId"),
        message="Appointment booked successfully." if success else "",
    )


def _first(
    raw: dict[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return default


def _maybe_pid(raw: dict[str, Any], *keys: str) -> str | None:
    value = _first(raw, *keys)
    return pid(value) if value is not None else None


def _minimum_extra(raw: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "ContactId",
        "IsActive",
        "PreferredLanguage",
        "RecallLength",
        "RecallInterval",
        "LastVisit",
        "UpdatedAt",
    )
    return {key: raw[key] for key in keep if key in raw}
