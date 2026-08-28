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

from src.app.retell.pii import contains_retell_pii_placeholder

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

# Every trigger type in ``definition_schema.WorkflowTrigger`` must appear here.
# ``fields_for`` filters on membership, so a trigger missing from this Literal
# resolves to an EMPTY catalog: no insert-field menu in the builder, and a
# ``merge_field_unavailable_for_trigger`` warning on every token in the workflow.
# ``test_merge_field_catalog_coverage`` enforces the correspondence.
WorkflowTriggerType = Literal[
    "appointment_offset",
    "appointment_state_changed",
    "recall_scan",
    "manual",
    "bulk_import",
    "callback_requested",
    "patient_status_changed",
    "sms_reply",
    "email_reply",
]
MergeChannel = Literal["sms", "email", "voice"]
MergeAvailability = Literal["required_context", "optional_context", "derived"]
MergePhiLevel = Literal["none", "low", "medium", "high"]
MergeFieldSource = Literal["contact", "location", "context", "derived"]

ALL_TRIGGERS: tuple[WorkflowTriggerType, ...] = (
    "appointment_offset",
    "appointment_state_changed",
    "recall_scan",
    "manual",
    "bulk_import",
    "callback_requested",
    "patient_status_changed",
    "sms_reply",
    "email_reply",
)

# Triggers whose run context carries an appointment. ``appointment_state_changed``
# is the GoTracker/NexHealth appointment-state trigger and carries the richest
# appointment context of all three, so it belongs here.
APPOINTMENT_TRIGGERS: tuple[WorkflowTriggerType, ...] = (
    "appointment_offset",
    "appointment_state_changed",
    "patient_status_changed",
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
        if raw is not None and raw != "" and not contains_retell_pii_placeholder(raw):
            return str(raw)
    return ""


def _nested(context: dict[str, Any], section: str, *keys: str) -> str:
    raw_section = context.get(section)
    if not isinstance(raw_section, dict):
        return ""
    for key in keys:
        raw = raw_section.get(key)
        if raw is not None and raw != "" and not contains_retell_pii_placeholder(raw):
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
        for candidate in (
            contact.full_name,
            f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
        ):
            if candidate and not contains_retell_pii_placeholder(candidate):
                return candidate
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


def _contact_value_or_context(
    contact: "Contact | None",
    attr: str,
    context: dict[str, Any],
    *keys: str,
) -> str:
    if contact is not None:
        raw = getattr(contact, attr, None)
        if raw is not None and raw != "" and not contains_retell_pii_placeholder(raw):
            return str(raw)
    return _value(context, *keys)


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
            raw.setdefault("appointment_status_id", _nested(raw, "appointment", "status_id", "StatusId"))
            raw.setdefault("appointment_reason", _nested(raw, "appointment", "reason", "Reason", "appointment_reason"))
            raw.setdefault("appointment_duration", _nested(raw, "appointment", "duration", "Duration"))
            raw.setdefault("appointment_type", _nested(raw, "appointment", "appointment_type", "appointment_type_name", "type", "Type"))
            raw.setdefault("appointment_type_name", _nested(raw, "appointment", "appointment_type_name", "appointment_type", "type", "Type"))
            raw.setdefault("provider_id", _nested(raw, "appointment", "provider_id", "ProviderId"))
            raw.setdefault("provider_name", _nested(raw, "appointment", "provider_name", "ProviderName"))
            raw.setdefault("schedule_column_id", _nested(raw, "appointment", "schedule_column_id", "ScheduleColumnId"))
            raw.setdefault("booked_user_id", _nested(raw, "appointment", "booked_user_id", "BookedUserId"))
            raw.setdefault("booked_timestamp", _nested(raw, "appointment", "booked_timestamp", "BookedTimeStamp"))
            raw.setdefault("created_machine_name", _nested(raw, "appointment", "created_machine_name", "CreatedMachineName"))

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
            raw.setdefault("patient_first_name", _nested(raw, "patient", "first_name", "FirstName", "firstName"))
            raw.setdefault("patient_last_name", _nested(raw, "patient", "last_name", "LastName", "lastName"))
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
        resolve=lambda c, _l, _ctx: _contact_value_or_context(c, "first_name", _ctx, "patient_first_name", "first_name"),
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
        resolve=lambda c, _l, _ctx: _contact_value_or_context(c, "last_name", _ctx, "patient_last_name", "last_name"),
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
        triggers=APPOINTMENT_TRIGGERS,
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
        triggers=APPOINTMENT_TRIGGERS,
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
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_appointment_datetime,
    ),
    MergeFieldSpec(
        name="appointment_reason",
        label="Appointment reason",
        description="The appointment reason supplied by the PMS.",
        sample="bridge prep",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.reason",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("appointment_reason"),
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
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("appointment_status"),
    ),
    MergeFieldSpec(
        name="appointment_status_id",
        label="Appointment status ID",
        description="The source appointment status identifier.",
        sample="1",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.status_id",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("appointment_status_id"),
    ),
    MergeFieldSpec(
        name="appointment_duration",
        label="Appointment duration",
        description="The source appointment duration.",
        sample="00:15:00",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.duration",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("appointment_duration"),
    ),
    MergeFieldSpec(
        name="provider_id",
        label="Provider ID",
        description="The source provider identifier.",
        sample="gotracker:123",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.provider_id",),
        phi_level="low",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("provider_id"),
    ),
    MergeFieldSpec(
        name="schedule_column_id",
        label="Schedule column ID",
        description="The GoTracker schedule column or chair identifier.",
        sample="1",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.schedule_column_id",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("schedule_column_id"),
    ),
    MergeFieldSpec(
        name="booked_user_id",
        label="Booked user",
        description="The PMS user that booked the appointment when available.",
        sample="Admin",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.booked_user_id",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("booked_user_id"),
    ),
    MergeFieldSpec(
        name="booked_timestamp",
        label="Booked timestamp",
        description="The timestamp when the PMS appointment was booked.",
        sample="2026-07-29T20:32:00.810",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.booked_timestamp",),
        phi_level="medium",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("booked_timestamp"),
    ),
    MergeFieldSpec(
        name="created_machine_name",
        label="Created machine",
        description="The machine name recorded by GoTracker when the appointment was created.",
        sample="EC2AMAZ-QKGJ1Q1",
        group="appointment",
        source="context",
        availability="optional_context",
        requires=("appointment.created_machine_name",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=APPOINTMENT_TRIGGERS,
        resolve=_context_field("created_machine_name"),
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
    MergeFieldSpec(
        name="sms_reply_body",
        label="SMS reply body",
        description="The inbound text that started an SMS reply workflow.",
        sample="I need to reschedule",
        group="sms_reply",
        source="context",
        availability="required_context",
        requires=("sms_reply_body",),
        phi_level="high",
        channels=("email", "voice"),
        triggers=("sms_reply",),
        resolve=_context_field("sms_reply_body"),
    ),
    MergeFieldSpec(
        name="sms_reply_intent",
        label="SMS reply intent",
        description="The deterministic intent classifier result for the inbound SMS.",
        sample="free_text",
        group="sms_reply",
        source="context",
        availability="required_context",
        requires=("sms_reply_intent",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=("sms_reply",),
        resolve=_context_field("sms_reply_intent"),
    ),
    MergeFieldSpec(
        name="inbound_sms_message_id",
        label="Inbound SMS message ID",
        description="The internal inbound SMS record id.",
        sample="inbound-1",
        group="sms_reply",
        source="context",
        availability="required_context",
        requires=("inbound_sms_message_id",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=("sms_reply",),
        resolve=_context_field("inbound_sms_message_id"),
    ),
    # Email counterparts. The inbound-email resume path writes these two keys
    # into run context (`tasks/inbound_email.py`), so they are what an
    # email-reply workflow can actually address. The message body is not
    # exposed: unlike an SMS reply it can be arbitrarily long and quote prior
    # correspondence, so it is not a safe merge value for outbound copy.
    MergeFieldSpec(
        name="email_reply_intent",
        label="Email reply intent",
        description="The classifier result for the inbound patient email.",
        sample="reschedule_request",
        group="email_reply",
        source="context",
        availability="required_context",
        requires=("email_reply_intent",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=("email_reply",),
        resolve=_context_field("email_reply_intent"),
    ),
    MergeFieldSpec(
        name="email_reply_message_id",
        label="Inbound email message ID",
        description="The internal inbound email record id.",
        sample="inbound-email-1",
        group="email_reply",
        source="context",
        availability="required_context",
        requires=("email_reply_message_id",),
        phi_level="none",
        channels=ALL_CHANNELS,
        triggers=("email_reply",),
        resolve=_context_field("email_reply_message_id"),
    ),
)

STATIC_MERGE_FIELDS: tuple[MergeFieldSpec, ...] = tuple(
    field for field in MERGE_FIELD_CATALOG if field.source in {"contact", "location"}
)
