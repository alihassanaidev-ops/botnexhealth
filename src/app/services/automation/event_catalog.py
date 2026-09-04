"""Canonical event keys and the typed context each one carries.

The workflow builder used to speak GoTracker. Every appointment field an author
could branch on was a Tracker payload field — ``gotracker_status_id``,
``in_chair``, ``booked_machine_name`` — so a workflow written against them
evaluated to ``null`` on a NexHealth location and silently took the false
branch. NexHealth was made to impersonate GoTracker instead: the completed-visit
sweep writes the literal Chair Flow string ``"Completed"`` so it can ride the
GoTracker trigger.

This module is the vocabulary that replaces that. An **event key** names
something that happened in clinic terms, and each key declares the canonical
context fields it carries. Both PMS adapters translate into these; neither one's
native shape is the vocabulary.

Native payloads are not thrown away — they stay addressable under ``raw.*`` and
are marked PMS-specific so the builder can warn that a workflow using them will
not port. The point is that an author never *has* to reach for them.

``pms_support`` records, per field, whether a PMS supplies it natively, derives
it, or cannot supply it at all. The builder greys out what the current location
cannot provide, instead of letting someone publish a campaign that silently
never matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventKey = Literal[
    "appointment.booked",
    "appointment.rescheduled",
    "appointment.cancelled",
    "appointment.confirmed",
    "appointment.no_show",
    "appointment.checked_in",
    "appointment.completed",
    "appointment.reminder_due",
    "patient.recall_due",
    "patient.status_changed",
    "call.inbound.completed",
    "call.outbound.completed",
    "call.missed",
    "message.sms.inbound",
    "message.email.inbound",
    "enquiry.received",
    "schedule.tick",
    "external.webhook",
    "campaign.run.finished",
]

FieldType = Literal["string", "number", "boolean", "datetime", "date", "list", "object"]

#: How well a PMS can supply a field.
#:
#: ``native``      — the PMS sends it directly.
#: ``derived``     — we compute it (e.g. NexHealth visit completion from
#:                   start time plus type duration, since it emits no checkout).
#: ``unsupported`` — the PMS has no equivalent; the field will be absent.
PmsSupport = Literal["native", "derived", "unsupported"]

PmsName = Literal["gotracker", "nexhealth"]

_ALL_NATIVE: dict[str, PmsSupport] = {"gotracker": "native", "nexhealth": "native"}

#: Events that do not originate in a practice-management system at all — they
#: come from the platform itself. Declaring them native everywhere keeps the
#: builder from greying them out on a PMS basis that does not apply.
_PLATFORM_NATIVE: dict[str, PmsSupport] = dict(_ALL_NATIVE)


@dataclass(frozen=True)
class ContextFieldSpec:
    """One canonical field a workflow may read."""

    path: str
    label: str
    type: FieldType
    description: str
    sample: Any
    pms_support: dict[str, PmsSupport]
    # Mirrors the merge-field catalog's classification so the builder can warn
    # before clinical detail is put on SMS or read aloud by a voice agent.
    phi_level: Literal["none", "low", "medium", "high"] = "none"
    #: True for fields under ``raw.*``: usable, but they do not port across PMSs.
    pms_specific: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "label": self.label,
            "type": self.type,
            "description": self.description,
            "sample": self.sample,
            "pms_support": dict(self.pms_support),
            "phi_level": self.phi_level,
            "pms_specific": self.pms_specific,
        }


def _field(
    path: str,
    label: str,
    type_: FieldType,
    description: str,
    sample: Any,
    *,
    support: dict[str, PmsSupport] | None = None,
    phi_level: Literal["none", "low", "medium", "high"] = "none",
) -> ContextFieldSpec:
    return ContextFieldSpec(
        path=path,
        label=label,
        type=type_,
        description=description,
        sample=sample,
        pms_support=support or dict(_ALL_NATIVE),
        phi_level=phi_level,
    )


# ---------------------------------------------------------------------------
# Field groups, shared across the events that carry them
# ---------------------------------------------------------------------------

PATIENT_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("patient.id", "Patient ID", "string", "Local contact id.", "c-8821"),
    _field(
        "patient.first_name", "Patient first name", "string", "Given name.", "Jordan",
        phi_level="low",
    ),
    _field(
        "patient.last_name", "Patient last name", "string", "Family name.", "Rivera",
        phi_level="low",
    ),
    _field(
        "patient.preferred_language",
        "Preferred language",
        "string",
        "Language the patient prefers to be contacted in, when recorded.",
        "en",
        support={"gotracker": "unsupported", "nexhealth": "native"},
    ),
)

APPOINTMENT_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("appointment.id", "Appointment ID", "string", "PMS appointment id.", "1343"),
    _field(
        "appointment.start_at",
        "Appointment start",
        "datetime",
        "Start instant. Naive values are read in the clinic's timezone.",
        "2026-09-04T14:15:00",
        phi_level="medium",
    ),
    _field(
        "appointment.duration_minutes",
        "Duration (minutes)",
        "number",
        "Scheduled length.",
        15,
    ),
    _field(
        "appointment.status",
        "Appointment status",
        "string",
        "PMS-neutral status: booked, waiting, late, cancelled, no_show, pending.",
        "booked",
    ),
    _field(
        "appointment.reason",
        "Appointment reason",
        "string",
        "Primary reason or procedure label.",
        "implant surgery",
        phi_level="medium",
    ),
    _field(
        "appointment.reasons",
        "All appointment reasons",
        "list",
        "Every reason on the appointment, when the PMS sends more than one.",
        ["implant surgery"],
        phi_level="medium",
    ),
    _field(
        "appointment.is_confirmed",
        "Confirmed",
        "boolean",
        "Whether the patient has confirmed attendance.",
        False,
        support={"gotracker": "native", "nexhealth": "derived"},
    ),
    _field(
        "appointment.is_recall",
        "Is a recall visit",
        "boolean",
        "Whether the PMS flags this as a recall appointment.",
        False,
        support={"gotracker": "native", "nexhealth": "unsupported"},
    ),
    _field(
        "appointment.provider.id", "Provider ID", "string", "Treating provider.", "gt-2"
    ),
    _field(
        "appointment.provider.name",
        "Provider name",
        "string",
        "Treating provider's display name, when synced.",
        "Dr Chan",
        support={"gotracker": "derived", "nexhealth": "native"},
    ),
    _field(
        "appointment.type.id", "Appointment type ID", "string", "PMS type id.", "at-4",
        support={"gotracker": "unsupported", "nexhealth": "native"},
    ),
    _field(
        "appointment.type.name",
        "Appointment type",
        "string",
        "Named appointment type. GoTracker sends reasons rather than typed appointments.",
        "Implant consult",
        support={"gotracker": "unsupported", "nexhealth": "native"},
    ),
    _field(
        "appointment.original_start_at",
        "Original start",
        "datetime",
        "Start time before the most recent reschedule.",
        "2026-09-01T10:00:00",
        support={"gotracker": "native", "nexhealth": "derived"},
        phi_level="medium",
    ),
)

LOCATION_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("location.id", "Location ID", "string", "Local location id.", "loc-1"),
    _field("location.name", "Location name", "string", "Clinic display name.", "Riverside Dental"),
    _field("location.timezone", "Location timezone", "string", "IANA timezone.", "America/Toronto"),
)

_VISIT_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field(
        "visit.completed_at",
        "Visit completed at",
        "datetime",
        (
            "When the visit finished. GoTracker reports this through Chair Flow; "
            "NexHealth emits no checkout event, so it is derived from start time "
            "plus the appointment type's duration."
        ),
        "2026-09-04T15:00:00",
        support={"gotracker": "native", "nexhealth": "derived"},
        phi_level="medium",
    ),
)

_CALL_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("call.id", "Call ID", "string", "Local call id.", "call-771"),
    _field("call.direction", "Direction", "string", "inbound or outbound.", "inbound"),
    _field(
        "call.outcome",
        "Call outcome",
        "string",
        "Normalized disposition, e.g. confirmed, no_answer, needs_callback.",
        "needs_callback",
    ),
    _field(
        "call.duration_seconds", "Call duration (seconds)", "number", "Connected time.", 84
    ),
    _field(
        "call.callback_at",
        "Requested callback time",
        "datetime",
        "When the patient asked to be called back, if they said.",
        "2026-09-04T15:00:00",
    ),
)

_MESSAGE_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("message.id", "Message ID", "string", "Local inbound message id.", "inbound-1"),
    _field("message.channel", "Channel", "string", "sms or email.", "sms"),
    _field(
        "message.body",
        "Message body",
        "string",
        "What the patient wrote.",
        "I need to reschedule",
        phi_level="high",
    ),
    _field(
        "message.intent",
        "Detected intent",
        "string",
        "Classifier result for the inbound message.",
        "reschedule_request",
    ),
)

# GoTracker has no recall table. Its recalls endpoint derives overdue candidates
# from appointment history, which is why the scanner refuses to trust it until
# the synchronizer reports history sync complete. That makes recall `derived`
# there rather than unsupported — the campaign works, the provenance differs.
_RECALL_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field(
        "recall.due_at",
        "Recall due date",
        "date",
        "When the patient is next due.",
        "2026-09-15",
        support={"gotracker": "derived", "nexhealth": "native"},
        phi_level="medium",
    ),
    _field(
        "recall.type",
        "Recall type",
        "string",
        "Hygiene, perio, and so on. GoTracker derives recall from appointment "
        "history and does not carry a recall type.",
        "Hygiene",
        support={"gotracker": "unsupported", "nexhealth": "native"},
        phi_level="high",
    ),
    _field(
        "recall.last_visit_at",
        "Last visit",
        "date",
        "Most recent completed visit.",
        "2026-03-02",
        support={"gotracker": "derived", "nexhealth": "native"},
        phi_level="high",
    ),
)

# An enquiry is a platform-level lead, not a PMS record: it arrives through the
# intake pipeline (a token endpoint, or a staff member typing a phone enquiry).
_ENQUIRY_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field(
        "enquiry.source",
        "Enquiry source",
        "string",
        "Where the enquiry came from, as recorded by the intake source.",
        "phone",
    ),
    _field(
        "enquiry.status",
        "Lead status",
        "string",
        "The contact's lead status at the moment the enquiry landed.",
        "new",
    ),
    _field(
        "enquiry.created",
        "Created a new contact",
        "boolean",
        "True when this enquiry is the first time we have seen this person.",
        True,
    ),
    _field(
        "enquiry.matched_existing_contact",
        "Matched an existing contact",
        "boolean",
        "True when the enquiry was linked to a contact we already had.",
        False,
    ),
)

# The internal-status trigger reports the transition, not just the landing
# state: authors routinely want "moved *to* Completed" but sometimes need
# "moved from Pending to anything".
_INTERNAL_STATUS_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field(
        "patient.status",
        "New status",
        "string",
        "The status the record moved to.",
        "appointment_confirmed",
    ),
    _field(
        "patient.status_previous",
        "Previous status",
        "string",
        "The status it moved from. Absent when the record had none.",
        "engaged",
    ),
    _field(
        "patient.status_field",
        "Status field",
        "string",
        "Which tracked field changed: call_workflow_status, contact_lead_status "
        "or handoff_status.",
        "contact_lead_status",
    ),
)

# Namespaced `trigger.*` rather than `event.*`: trigger payloads already carry a
# flat `event` string (the native webhook name), and a canonical object under the
# same key would shadow it.
_COMMON_FIELDS: tuple[ContextFieldSpec, ...] = (
    _field("trigger.key", "Event", "string", "Which event started this run.", "appointment.booked"),
    _field("trigger.occurred_at", "Event time", "datetime", "When it happened.", "2026-09-01T09:00:00Z"),
    _field("trigger.source_pms", "Source PMS", "string", "gotracker, nexhealth, or none.", "gotracker"),
)


# ---------------------------------------------------------------------------
# Event → context
# ---------------------------------------------------------------------------

_APPOINTMENT_CONTEXT = (
    _COMMON_FIELDS + PATIENT_FIELDS + APPOINTMENT_FIELDS + LOCATION_FIELDS
)

EVENT_CONTEXT: dict[str, tuple[ContextFieldSpec, ...]] = {
    "appointment.booked": _APPOINTMENT_CONTEXT,
    "appointment.rescheduled": _APPOINTMENT_CONTEXT,
    "appointment.cancelled": _APPOINTMENT_CONTEXT,
    "appointment.confirmed": _APPOINTMENT_CONTEXT,
    "appointment.no_show": _APPOINTMENT_CONTEXT,
    "appointment.checked_in": _APPOINTMENT_CONTEXT,
    "appointment.completed": _APPOINTMENT_CONTEXT + _VISIT_FIELDS,
    "appointment.reminder_due": _APPOINTMENT_CONTEXT,
    "patient.recall_due": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _RECALL_FIELDS,
    # Appointment fields stay declared here because a status change recorded by
    # a campaign inherits the source run's context, which usually has them.
    "patient.status_changed": _APPOINTMENT_CONTEXT + _INTERNAL_STATUS_FIELDS,
    "call.inbound.completed": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _CALL_FIELDS,
    "call.outbound.completed": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _CALL_FIELDS,
    "call.missed": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _CALL_FIELDS,
    "message.sms.inbound": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _MESSAGE_FIELDS,
    "message.email.inbound": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _MESSAGE_FIELDS,
    "enquiry.received": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _ENQUIRY_FIELDS,
    "schedule.tick": _COMMON_FIELDS + PATIENT_FIELDS + LOCATION_FIELDS + _RECALL_FIELDS,
    "external.webhook": _COMMON_FIELDS
    + LOCATION_FIELDS
    + (
        _field(
            "payload",
            "Webhook payload",
            "object",
            "The body posted to the workflow webhook endpoint.",
            {"lead_id": "abc"},
        ),
    ),
    "campaign.run.finished": _COMMON_FIELDS
    + PATIENT_FIELDS
    + LOCATION_FIELDS
    + (
        _field(
            "run.outcome",
            "Previous run outcome",
            "string",
            "Outcome recorded by the campaign that just finished.",
            "appointment_confirmed",
        ),
        _field("run.workflow_id", "Previous campaign", "string", "Which campaign finished.", "wf-1"),
    ),
}

ALL_EVENT_KEYS: tuple[str, ...] = tuple(EVENT_CONTEXT)


@dataclass(frozen=True)
class EventSpec:
    key: str
    label: str
    description: str
    #: Whether a PMS can raise this event at all.
    pms_support: dict[str, PmsSupport]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "pms_support": dict(self.pms_support),
            "context": [field.as_dict() for field in EVENT_CONTEXT[self.key]],
        }


EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        "appointment.booked",
        "Appointment booked",
        "A new appointment was scheduled.",
        dict(_ALL_NATIVE),
    ),
    EventSpec(
        "appointment.rescheduled",
        "Appointment rescheduled",
        "An existing appointment moved to a new time.",
        dict(_ALL_NATIVE),
    ),
    EventSpec(
        "appointment.cancelled",
        "Appointment cancelled",
        "The appointment was cancelled by the patient or the practice.",
        dict(_ALL_NATIVE),
    ),
    EventSpec(
        "appointment.confirmed",
        "Appointment confirmed",
        "The patient confirmed they will attend.",
        {"gotracker": "native", "nexhealth": "derived"},
    ),
    EventSpec(
        "appointment.no_show",
        "Patient did not attend",
        "The appointment time passed without the patient attending.",
        {"gotracker": "native", "nexhealth": "derived"},
    ),
    EventSpec(
        "appointment.checked_in",
        "Patient checked in",
        "The patient arrived and was checked in.",
        {"gotracker": "native", "nexhealth": "unsupported"},
    ),
    EventSpec(
        "appointment.completed",
        "Visit completed",
        (
            "The visit finished. GoTracker reports it through Chair Flow; for "
            "NexHealth it is derived, because no checkout event exists."
        ),
        {"gotracker": "native", "nexhealth": "derived"},
    ),
    EventSpec(
        "appointment.reminder_due",
        "Reminder due",
        "A configured interval before or after an appointment was reached.",
        dict(_ALL_NATIVE),
    ),
    EventSpec(
        "patient.recall_due",
        "Recall due",
        (
            "The patient is due or overdue for a recall visit. NexHealth reports "
            "recalls directly; GoTracker derives them from appointment history, "
            "so its recall campaigns wait for history sync to complete."
        ),
        {"gotracker": "derived", "nexhealth": "native"},
    ),
    EventSpec(
        "patient.status_changed",
        "Internal status changed",
        "A status field the platform owns moved to a new value.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "call.inbound.completed",
        "Inbound call ended",
        "A patient's call to the clinic finished.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "call.outbound.completed",
        "Outbound call ended",
        "A call the platform placed finished, with a disposition.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "call.missed",
        "Call missed",
        "An inbound call was not answered.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "message.sms.inbound",
        "SMS received",
        "A patient texted the clinic.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "message.email.inbound",
        "Email received",
        "A patient replied to a clinic email.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "enquiry.received",
        "Enquiry received",
        "A lead arrived through the intake pipeline.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "schedule.tick",
        "On a schedule",
        "A recurring time-based trigger fired.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "external.webhook",
        "External webhook",
        "Something outside the platform posted to this campaign's webhook.",
        dict(_PLATFORM_NATIVE),
    ),
    EventSpec(
        "campaign.run.finished",
        "Campaign finished",
        "Another campaign's run reached an exit, so this one can chain from it.",
        dict(_PLATFORM_NATIVE),
    ),
)

_EVENTS_BY_KEY = {event.key: event for event in EVENTS}


def event_spec(key: str) -> EventSpec | None:
    return _EVENTS_BY_KEY.get(key)


def context_fields(key: str) -> tuple[ContextFieldSpec, ...]:
    return EVENT_CONTEXT.get(key, ())


def supports(key: str, pms: str) -> PmsSupport:
    """Whether ``pms`` can raise this event."""
    spec = _EVENTS_BY_KEY.get(key)
    if spec is None:
        return "unsupported"
    return spec.pms_support.get(pms, "unsupported")


def public_events(pms: str | None = None) -> list[dict[str, Any]]:
    """Serializable catalog for the API and the builder.

    Filtering by ``pms`` drops events that PMS cannot raise, so the builder's
    trigger picker only offers what the selected location can actually deliver.
    """
    return [
        event.as_dict()
        for event in EVENTS
        if pms is None or event.pms_support.get(pms, "unsupported") != "unsupported"
    ]


def sample_context(key: str) -> dict[str, Any]:
    """Nested sample values for previews and dry runs."""
    sample: dict[str, Any] = {}
    for field in context_fields(key):
        _assign(sample, field.path, field.sample)
    return sample


def _assign(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value
