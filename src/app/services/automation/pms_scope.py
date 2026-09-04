"""PMS ownership map for workflow triggers and nodes.

A trigger or node is either shared across every practice-management system or
owned by exactly one of them. ``update_gotracker_appointment`` only runs against
a GoTracker location, so publishing it for a NexHealth institution produces a
step that can never execute.

Triggers are no longer gated here — every one of the six is PMS-neutral, and
per-PMS availability is decided per *event key* by ``event_catalog.supports``.
That is a finer instrument: the old map had to hide a whole trigger from a
tenant because one of the states it matched was GoTracker-only.

This module is the single source of truth: the builder-capabilities endpoint,
publish validation, the launch checklist, and template filtering all read from
it, and ``tests/unit/test_workflow_schema_frontend_parity.py`` asserts the
frontend catalog mirrors it exactly. Every member of the schema's trigger and
node unions must appear here — the parity test fails when a new type is added
without a PMS classification.
"""

from __future__ import annotations

ALL_PMS_TYPES: frozenset[str] = frozenset({"nexhealth", "gotracker", "none"})
_GOTRACKER_ONLY: frozenset[str] = frozenset({"gotracker"})

#: Every current trigger is available on every PMS.
#:
#: This map used to carry the per-PMS gating, which is why
#: ``appointment_state_changed`` was GoTracker-only: the trigger itself was
#: GoTracker-shaped, so offering it to a NexHealth tenant built a campaign that
#: silently never enrolled anyone. Triggers are PMS-neutral now, and the gating
#: moved down to the individual event key, where it belongs — see
#: ``event_catalog.supports``. A NexHealth tenant gets the event trigger; it
#: just is not offered ``appointment.checked_in`` inside it.
TRIGGER_PMS: dict[str, frozenset[str]] = {
    "event": ALL_PMS_TYPES,
    "manual": ALL_PMS_TYPES,
    "form_submitted": ALL_PMS_TYPES,
    "internal_status": ALL_PMS_TYPES,
    "schedule": ALL_PMS_TYPES,
    "inbound_message": ALL_PMS_TYPES,
}

NODE_PMS: dict[str, frozenset[str]] = {
    "wait": ALL_PMS_TYPES,
    "wait_for_sms_reply": ALL_PMS_TYPES,
    "drip": ALL_PMS_TYPES,
    "send_sms": ALL_PMS_TYPES,
    "retell_sms_conversation": ALL_PMS_TYPES,
    "send_voice": ALL_PMS_TYPES,
    "send_email": ALL_PMS_TYPES,
    "update_patient_status": ALL_PMS_TYPES,
    "update_gotracker_appointment": _GOTRACKER_ONLY,
    "update_appointment": ALL_PMS_TYPES,
    "book_appointment": ALL_PMS_TYPES,
    "booking_link": ALL_PMS_TYPES,
    "patient_registration": ALL_PMS_TYPES,
    "json_mapper": ALL_PMS_TYPES,
    "llm": ALL_PMS_TYPES,
    "condition": ALL_PMS_TYPES,
    "switch": ALL_PMS_TYPES,
    "split": ALL_PMS_TYPES,
    "exit": ALL_PMS_TYPES,
}


def trigger_allowed(trigger_type: str, pms_type: str) -> bool:
    return pms_type in TRIGGER_PMS.get(trigger_type, ALL_PMS_TYPES)


def node_allowed(node_type: str, pms_type: str) -> bool:
    return pms_type in NODE_PMS.get(node_type, ALL_PMS_TYPES)


def allowed_trigger_types(pms_type: str) -> list[str]:
    return [t for t, allowed in TRIGGER_PMS.items() if pms_type in allowed]


def allowed_node_types(pms_type: str) -> list[str]:
    return [n for n, allowed in NODE_PMS.items() if pms_type in allowed]
