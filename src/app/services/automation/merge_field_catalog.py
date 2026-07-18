"""Dental-aware merge-field catalog and normalized render context.

The backend is the source of truth for fields the workflow builder may insert.
The renderer remains permissive at final substitution time: missing values become
empty strings so raw ``{{token}}`` text never reaches patients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

WorkflowTriggerType = Literal[
    "appointment_offset",
    "recall_scan",
    "manual",
    "bulk_import",
    "callback_requested",
]
MergeChannel = Literal["sms", "email", "voice"]
MergeAvailability = Literal["required_context", "optional_context", "derived"]
MergePhiLevel = Literal["none", "low", "medium", "high"]
MergeFieldSource = Literal["contact", "location", "context", "derived"]

ALL_TRIGGERS: tuple[WorkflowTriggerType, ...] = (
    "appointment_offset",
    "recall_scan",
    "manual",
    "bulk_import",
    "callback_requested",
)
ALL_CHANNELS: tuple[MergeChannel, ...] = ("sms", "email", "voice")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class MergeFieldSpec:
    """A field the campaign renderer can substitute."""

    name: str
    label: str
    description: str
    sample: str
    group: str
    source: MergeFieldSource
    availability: MergeAvailability
    requires: tuple[str, ...]
    phi_level: MergePhiLevel
    channels: tuple[MergeChannel, ...]
    triggers: tuple[WorkflowTriggerType, ...]
    resolve: Callable[
        ["Contact | None", "InstitutionLocation | None", dict[str, Any]],
        str,
    ]

    @property
    def token(self) -> str:
        return "{{" + self.name + "}}"


def _value(context: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = context.get(key)
        if raw is not None and raw != "":
            return str(raw)
    return ""


def _nested(context: dict[str, Any], section: str, *keys: str) -> str:
    raw_section = context.get(section)
    if not isinstance(raw_section, dict):
        return ""
    for key in keys:
        raw = raw_section.get(key)
        if raw is not None and raw != "":
            return str(raw)
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_date(value: Any) -> str:
    text = str(value or "")
    if _DATE_RE.match(text):
        return text
    dt = _parse_datetime(value)
    if dt is None:
        return text
    return dt.strftime("%B %-d, %Y")


def _format_time(value: Any) -> str:
    dt = _parse_datetime(value)
    if dt is None:
        return str(value or "")
    return dt.strftime("%-I:%M %p")


def _full_name(contact: "Contact | None", _location: "InstitutionLocation | None", _ctx: dict[str, Any]) -> str:
    if contact is not None:
        return (
            contact.full_name
            or f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        )
    return _value(_ctx, "patient_full_name")


def _location_address(_contact: "Contact | None", location: "InstitutionLocation | None", _ctx: dict[str, Any]) -> str:
    if location is not None:
        parts = [
            getattr(location, "address", None),
            getattr(location, "city", None),
            getattr(location, "state", None),
        ]
        return ", ".join(str(p).strip() for p in parts if p)
    return _value(_ctx, "location_address")


def _context_field(name: str) -> Callable[["Contact | None", "InstitutionLocation | None", dict[str, Any]], str]:
    def _resolve(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
        return _value(context, name)

    return _resolve


def _appointment_date(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "appointment_date") or _format_date(
        context.get("appointment_datetime")
        or context.get("appointment_start_time")
        or context.get("appointment_at")
    )


def _appointment_time(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "appointment_time") or _format_time(
        context.get("appointment_datetime")
        or context.get("appointment_start_time")
        or context.get("appointment_at")
    )


def _appointment_datetime(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    explicit = _value(context, "appointment_datetime")
    if explicit and _parse_datetime(explicit) is None:
        return explicit
    return _format_datetime_for_patient(
        explicit
        or context.get("appointment_start_time")
        or context.get("appointment_at")
    )


def _recall_due_date(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "recall_due_date") or _format_date(
        context.get("due_date")
        or context.get("recall_at")
    )


def _last_visit_date(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "last_visit_date") or _format_date(context.get("last_visit_at"))


def _callback_requested_at(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "callback_requested_at") or _format_datetime_for_patient(
        context.get("requested_at")
    )


def _preferred_callback_time(_contact: "Contact | None", _location: "InstitutionLocation | None", context: dict[str, Any]) -> str:
    return _value(context, "preferred_callback_time") or _format_datetime_for_patient(
        context.get("preferred_callback_at")
    )


def _format_datetime_for_patient(value: Any) -> str:
    dt = _parse_datetime(value)
    if dt is None:
        return str(value or "")
    return f"{dt.strftime('%B %-d, %Y')} at {dt.strftime('%-I:%M %p')}"


class MergeContextBuilder:
    """Build a flat render context from run metadata and nested trigger payloads."""

    @classmethod
    def build(
        cls,
        *,
        contact: "Contact | None" = None,
        location: "InstitutionLocation | None" = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        raw = cls.normalize_raw_context(context)
        flat: dict[str, str] = {}

        for field in MERGE_FIELD_CATALOG:
            value = field.resolve(contact, location, raw)
            flat[field.name] = value

        for key, value in raw.items():
            if isinstance(value, dict):
                continue
            if isinstance(key, str) and key not in flat:
                flat[key] = str(value) if value is not None else ""

        return flat

    @staticmethod
    def normalize_raw_context(context: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(context or {})

        appointment = raw.get("appointment")
        if isinstance(appointment, dict):
            raw.setdefault("appointment_datetime", _nested(raw, "appointment", "datetime", "start_time", "start_at", "appointment_at"))
            raw.setdefault("appointment_status", _nested(raw, "appointment", "status"))
            raw.setdefault("appointment_type", _nested(raw, "appointment", "type", "appointment_type", "appointment_type_name"))
            raw.setdefault("provider_name", _nested(raw, "appointment", "provider_name", "provider"))
            raw.setdefault("operatory_name", _nested(raw, "appointment", "operatory_name", "operatory"))

        recall = raw.get("recall")
        if isinstance(recall, dict):
            raw.setdefault("recall_due_date", _nested(raw, "recall", "due_date", "recall_due_date"))
            raw.setdefault("recall_type", _nested(raw, "recall", "type", "recall_type"))
            raw.setdefault("last_visit_date", _nested(raw, "recall", "last_visit_date", "last_visit_at"))

        callback = raw.get("callback")
        if isinstance(callback, dict):
            raw.setdefault("callback_requested_at", _nested(raw, "callback", "requested_at", "callback_requested_at"))
            raw.setdefault("callback_reason", _nested(raw, "callback", "reason", "callback_reason"))
            raw.setdefault("preferred_callback_time", _nested(raw, "callback", "preferred_time", "preferred_callback_time", "preferred_callback_at"))

        booking = raw.get("booking")
        if isinstance(booking, dict):
            raw.setdefault("booking_link", _nested(raw, "booking", "booking_link", "url"))
            raw.setdefault("confirmation_link", _nested(raw, "booking", "confirmation_link", "confirm_url"))
            raw.setdefault("reschedule_link", _nested(raw, "booking", "reschedule_link", "reschedule_url"))

        patient = raw.get("patient")
        if isinstance(patient, dict):
            raw.setdefault("patient_preferred_language", _nested(raw, "patient", "preferred_language", "language"))
            raw.setdefault("guardian_first_name", _nested(raw, "patient", "guardian_first_name"))
            raw.setdefault("guardian_full_name", _nested(raw, "patient", "guardian_full_name"))

        return raw


def fields_for(
    *,
    trigger_type: str | None = None,
    channel: str | None = None,
    include_unavailable: bool = False,
) -> list[MergeFieldSpec]:
    """Return fields matching the requested trigger/channel scope."""
    return [
        field
        for field in MERGE_FIELD_CATALOG
        if (
            include_unavailable
            or trigger_type is None
            or trigger_type in field.triggers
        )
        and (channel is None or channel in field.channels)
    ]


MERGE_FIELD_CATALOG: tuple[MergeFieldSpec, ...] = (
    MergeFieldSpec(
        name="patient_first_name",
        label="Patient first name",
        description="The patient's first name.",
        sample="Jordan",
        group="patient",
        source="contact",
        availability="derived",
        requires=("contact.first_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=lambda c, _l, _ctx: (c.first_name or "") if c else _value(_ctx, "patient_first_name"),
    ),
    MergeFieldSpec(
        name="patient_last_name",
        label="Patient last name",
        description="The patient's last name.",
        sample="Rivera",
        group="patient",
        source="contact",
        availability="derived",
        requires=("contact.last_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=lambda c, _l, _ctx: (c.last_name or "") if c else _value(_ctx, "patient_last_name"),
    ),
    MergeFieldSpec(
        name="patient_full_name",
        label="Patient full name",
        description="The patient's full name.",
        sample="Jordan Rivera",
        group="patient",
        source="contact",
        availability="derived",
        requires=("contact.full_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=_full_name,
    ),
    MergeFieldSpec(
        name="patient_preferred_language",
        label="Preferred language",
        description="The patient's preferred language when available from PMS data.",
        sample="English",
        group="patient",
        source="context",
        availability="optional_context",
        requires=("patient.preferred_language",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=_context_field("patient_preferred_language"),
    ),
    MergeFieldSpec(
        name="guardian_first_name",
        label="Guardian first name",
        description="The first name of the patient's guardian when available.",
        sample="Alex",
        group="patient",
        source="context",
        availability="optional_context",
        requires=("patient.guardian_first_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=_context_field("guardian_first_name"),
    ),
    MergeFieldSpec(
        name="guardian_full_name",
        label="Guardian full name",
        description="The full name of the patient's guardian when available.",
        sample="Alex Rivera",
        group="patient",
        source="context",
        availability="optional_context",
        requires=("patient.guardian_full_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=_context_field("guardian_full_name"),
    ),
    MergeFieldSpec(
        name="appointment_date",
        label="Appointment date",
        description="The appointment date.",
        sample="July 22, 2026",
        group="appointment",
        source="context",
        availability="required_context",
        requires=("appointment.start_time",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=("appointment_offset",),
        resolve=_appointment_date,
    ),
    MergeFieldSpec(
        name="appointment_time",
        label="Appointment time",
        description="The appointment time.",
        sample="2:00 PM",
        group="appointment",
        source="context",
        availability="required_context",
        requires=("appointment.start_time",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=("appointment_offset",),
        resolve=_appointment_time,
    ),
    MergeFieldSpec(
        name="appointment_datetime",
        label="Appointment date and time",
        description="The appointment date and time.",
        sample="July 22, 2026 at 2:00 PM",
        group="appointment",
        source="context",
        availability="required_context",
        requires=("appointment.start_time",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=("appointment_offset",),
        resolve=_appointment_datetime,
    ),
    MergeFieldSpec(
        name="appointment_type",
        label="Appointment type",
        description="The appointment type when available from reference data.",
        sample="Cleaning",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.appointment_type",),
        phi_level="high",
        channels=("email",),
        triggers=("appointment_offset",),
        resolve=_context_field("appointment_type"),
    ),
    MergeFieldSpec(
        name="appointment_status",
        label="Appointment status",
        description="The current appointment status.",
        sample="scheduled",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.status",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=("appointment_offset",),
        resolve=_context_field("appointment_status"),
    ),
    MergeFieldSpec(
        name="provider_name",
        label="Provider name",
        description="The appointment provider name when available.",
        sample="Dr. Smith",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.provider_name",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=("appointment_offset",),
        resolve=_context_field("provider_name"),
    ),
    MergeFieldSpec(
        name="operatory_name",
        label="Operatory name",
        description="The appointment room or chair name when available.",
        sample="Operatory 3",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.operatory_name",),
        phi_level="medium",
        channels=("email", "voice"),
        triggers=("appointment_offset",),
        resolve=_context_field("operatory_name"),
    ),
    MergeFieldSpec(
        name="clinic_name",
        label="Clinic name",
        description="The name of the clinic/location.",
        sample="Riverside Dental",
        group="location",
        source="location",
        availability="derived",
        requires=("location.name",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=lambda _c, loc, _ctx: (loc.name or "") if loc else _value(_ctx, "clinic_name"),
    ),
    MergeFieldSpec(
        name="location_name",
        label="Location name",
        description="The name of the practice location.",
        sample="Riverside Dental - Downtown",
        group="location",
        source="location",
        availability="derived",
        requires=("location.name",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=lambda _c, loc, _ctx: (loc.name or "") if loc else _value(_ctx, "location_name"),
    ),
    MergeFieldSpec(
        name="location_phone",
        label="Location phone",
        description="The practice location phone number.",
        sample="(555) 010-2211",
        group="location",
        source="location",
        availability="derived",
        requires=("location.phone",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=ALL_TRIGGERS,
        resolve=lambda _c, loc, _ctx: (getattr(loc, "phone", None) or "") if loc else _value(_ctx, "location_phone"),
    ),
    MergeFieldSpec(
        name="location_address",
        label="Location address",
        description="The practice location mailing address.",
        sample="100 Main St, Austin, TX",
        group="location",
        source="location",
        availability="derived",
        requires=("location.address",),
        phi_level="none",
        channels=("email", "voice"),
        triggers=ALL_TRIGGERS,
        resolve=_location_address,
    ),
    MergeFieldSpec(
        name="booking_link",
        label="Booking link",
        description="A per-run booking link when generated for this campaign.",
        sample="https://book.example.com/r/jordan",
        group="booking",
        source="context",
        availability="required_context",
        requires=("booking.booking_link",),
        phi_level="low",
        channels=("sms", "email"),
        triggers=ALL_TRIGGERS,
        resolve=_context_field("booking_link"),
    ),
    MergeFieldSpec(
        name="confirmation_link",
        label="Confirmation link",
        description="A per-run appointment confirmation link.",
        sample="https://book.example.com/c/abc123",
        group="booking",
        source="context",
        availability="required_context",
        requires=("booking.confirmation_link",),
        phi_level="low",
        channels=("sms", "email"),
        triggers=("appointment_offset",),
        resolve=_context_field("confirmation_link"),
    ),
    MergeFieldSpec(
        name="reschedule_link",
        label="Reschedule link",
        description="A per-run appointment reschedule link.",
        sample="https://book.example.com/r/abc123",
        group="booking",
        source="context",
        availability="required_context",
        requires=("booking.reschedule_link",),
        phi_level="low",
        channels=("sms", "email"),
        triggers=("appointment_offset",),
        resolve=_context_field("reschedule_link"),
    ),
    MergeFieldSpec(
        name="recall_due_date",
        label="Recall due date",
        description="The patient's recall due date.",
        sample="August 15, 2026",
        group="recall",
        source="context",
        availability="required_context",
        requires=("recall.due_date",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=("recall_scan",),
        resolve=_recall_due_date,
    ),
    MergeFieldSpec(
        name="recall_type",
        label="Recall type",
        description="The recall type when available.",
        sample="Hygiene",
        group="recall",
        source="context",
        availability="optional_context",
        requires=("recall.type",),
        phi_level="high",
        channels=("email",),
        triggers=("recall_scan",),
        resolve=_context_field("recall_type"),
    ),
    MergeFieldSpec(
        name="last_visit_date",
        label="Last visit date",
        description="The patient's last visit date when available.",
        sample="February 12, 2026",
        group="recall",
        source="context",
        availability="optional_context",
        requires=("recall.last_visit_date",),
        phi_level="high",
        channels=("email",),
        triggers=("recall_scan",),
        resolve=_last_visit_date,
    ),
    MergeFieldSpec(
        name="callback_requested_at",
        label="Callback requested at",
        description="When the patient requested a callback.",
        sample="July 18, 2026 at 10:30 AM",
        group="callback",
        source="context",
        availability="required_context",
        requires=("callback.requested_at",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=("callback_requested",),
        resolve=_callback_requested_at,
    ),
    MergeFieldSpec(
        name="callback_reason",
        label="Callback reason",
        description="The normalized callback reason.",
        sample="Reschedule request",
        group="callback",
        source="context",
        availability="optional_context",
        requires=("callback.reason",),
        phi_level="medium",
        channels=("email", "voice"),
        triggers=("callback_requested",),
        resolve=_context_field("callback_reason"),
    ),
    MergeFieldSpec(
        name="preferred_callback_time",
        label="Preferred callback time",
        description="The patient's preferred callback time when captured.",
        sample="Today after 3:00 PM",
        group="callback",
        source="context",
        availability="optional_context",
        requires=("callback.preferred_time",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=("callback_requested",),
        resolve=_preferred_callback_time,
    ),
)

STATIC_MERGE_FIELDS: tuple[MergeFieldSpec, ...] = tuple(
    field for field in MERGE_FIELD_CATALOG if field.source in {"contact", "location"}
)
