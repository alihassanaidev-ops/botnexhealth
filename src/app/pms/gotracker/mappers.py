"""Map GoTracker Synchronizer responses to universal PMS models."""

from __future__ import annotations

from typing import Any

from src.app.pms.models import (
    BookingResult,
    BookingWriteStatus,
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
    return raw[len(prefix) :] if raw.startswith(prefix) else raw


def to_patient(raw: dict[str, Any]) -> UniversalPatient:
    raw_id = _first(raw, "ContactId", "contact_id", "id", "patient_id")
    first_name = _first(raw, "FirstName", "first_name", "firstName") or ""
    last_name = _first(raw, "LastName", "last_name", "lastName") or ""
    minimum_extra = _minimum_extra(raw)
    extra: dict[str, Any] = {"raw": minimum_extra}
    is_active = _first(raw, "IsActive", "is_active", "active")
    inactive = _first(raw, "Inactive", "inactive", "IsInactive")
    inactive_flag = _bool_or_none(inactive)
    active_flag = _bool_or_none(is_active)
    if inactive_flag is None and active_flag is not None:
        inactive_flag = not active_flag
    extra["inactive"] = bool(inactive_flag)
    updated_at = _first(
        raw,
        "ModifiedTimeStamp",
        "modified_timestamp",
        "updated_at",
        "UpdatedAt",
    )
    if updated_at not in (None, ""):
        extra["updated_at"] = str(updated_at)
    return UniversalPatient(
        id=pid(raw_id),
        source="gotracker",
        first_name=str(first_name),
        last_name=str(last_name),
        email=_first(raw, "Email", "email"),
        phone=_first(raw, "Phone", "phone", "PhoneNumber", "phone_number", "CellPhone"),
        date_of_birth=_first(raw, "BirthDate", "DateOfBirth", "DOB", "date_of_birth"),
        extra=extra,
    )


def to_upcoming_appointment(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a Tracker appointment to the safe Retell scheduling context.

    The voice agent needs an opaque appointment ID plus enough context for the
    caller to identify the visit.  Do not pass the raw Tracker record through:
    it contains fields unrelated to scheduling and may contain unnecessary PHI.
    """
    raw_id = _first(raw, "AppointmentId", "appointment_id", "id")
    provider_id = _first(raw, "ProviderId", "provider_id")
    location_id = _first(raw, "LocationId", "location_id", "lid")
    return {
        "id": pid(raw_id) if raw_id is not None else None,
        "provider_id": pid(provider_id) if provider_id is not None else None,
        "provider_name": _first(raw, "ProviderName", "provider_name"),
        "start_time": _appointment_start_time(raw),
        "end_time": _first(raw, "EndTime", "end_time"),
        "location_id": pid(location_id) if location_id is not None else None,
        "confirmed": _first(raw, "IsConfirmed", "confirmed"),
    }


def to_provider(raw: dict[str, Any]) -> UniversalProvider:
    raw_id = _first(raw, "ProviderId", "provider_id", "id")
    first_name = _first(raw, "FirstName", "first_name")
    last_name = _first(raw, "LastName", "last_name")
    name = _first(raw, "ProviderName", "provider_name", "Name", "name")
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
            "provider_ids": [
                pid(item)
                for item in raw.get("provider_ids") or raw.get("ProviderIds") or []
                if item is not None
            ],
            "operatory_ids": [
                pid(item)
                for item in raw.get("operatory_ids") or raw.get("OperatoryIds") or []
                if item is not None
            ],
            # Reasons are Tracker-native labels.  Keep their unprefixed IDs so
            # they line up with the cached reason rows (like NexHealth
            # descriptor IDs do today).
            "reason_ids": [
                str(item)
                for item in raw.get("reason_ids") or raw.get("ReasonIds") or []
                if item is not None
            ],
            "bookable_online": _first(
                raw, "bookable_online", "BookableOnline", default=True
            ),
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
    raw_location_id = _first(
        raw, "lid", "LocationId", "location_id", default=location_id
    )
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


#: What we tell a caller when the booking has not yet reached the practice.
PENDING_WRITE_MESSAGE = "We're confirming this with the practice and will let you know."
CONFIRMED_WRITE_MESSAGE = "Appointment booked successfully."


def _write_status(data: dict[str, Any], *, success: bool) -> str:
    """Read the Cloud Service's write status, defaulting to PENDING.

    The Cloud Service queues a booking until the clinic's machine is reachable,
    so acceptance alone does not mean the appointment exists in the practice's
    software. Until the response carries an explicit write status we must
    assume the write has not landed - the old behaviour assumed the opposite
    and reported every queued booking as though it were scheduled.
    """
    if not success:
        return BookingWriteStatus.UNKNOWN.value
    raw_status = _first(data, "write_status", "WriteStatus", "writeback_status")
    if raw_status is None:
        return BookingWriteStatus.PENDING.value
    normalised = str(raw_status).strip().lower()
    if normalised in {"confirmed", "written", "complete", "completed"}:
        return BookingWriteStatus.CONFIRMED.value
    if normalised in {"pending", "waiting", "queued", "accepted"}:
        return BookingWriteStatus.PENDING.value
    return BookingWriteStatus.UNKNOWN.value


def to_booking_result(raw: dict[str, Any], *, success: bool = True) -> BookingResult:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    appointment_id = _first(data, "appointment_id", "AppointmentId", "id")
    start = _first(data, "start_time", "StartTime")
    end = _first(data, "end_time", "EndTime")

    write_status = _write_status(data, success=success)
    is_pending = write_status == BookingWriteStatus.PENDING.value

    if success:
        # An explicit appointment status from the practice wins; otherwise the
        # booking is only as real as the write behind it.
        reported = _first(data, "status", "Status")
        status = (
            str(reported)
            if reported is not None
            else (BookingWriteStatus.PENDING.value if is_pending else "scheduled")
        )
        message = PENDING_WRITE_MESSAGE if is_pending else CONFIRMED_WRITE_MESSAGE
    else:
        status = "error"
        message = ""

    return BookingResult(
        success=success,
        id=pid(appointment_id) if appointment_id is not None else None,
        source="gotracker",
        status=status,
        write_status=write_status,
        start=start,
        end=end,
        patient_id=_maybe_pid(data, "patient_id", "PatientId", "ContactId"),
        provider_id=_maybe_pid(data, "provider_id", "ProviderId"),
        appointment_type_id=_maybe_pid(
            data, "appointment_type_id", "AppointmentTypeId"
        ),
        message=message,
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


def _appointment_start_time(raw: dict[str, Any]) -> str | None:
    direct = _first(raw, "StartTime", "start_time")
    if direct is not None:
        return str(direct)

    appointment_date = _first(raw, "AppointmentDate", "appointment_date")
    appointment_time = _first(raw, "AppointmentTime", "appointment_time")
    if appointment_date is None or appointment_time is None:
        return None
    return f"{str(appointment_date).split('T', 1)[0]}T{str(appointment_time).removesuffix('Z')}"


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


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None
