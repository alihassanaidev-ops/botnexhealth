"""The catalog must describe what the runtime actually produces.

This is the test that stops six field vocabularies growing back. The builder
offers whatever `event_catalog` declares; if the projection does not produce
those paths from a real payload, an author picks a field that is silently absent
at runtime and their condition takes the false branch forever.

Payloads here are trimmed from the real webhook fixtures — `test_gotracker_webhooks`
and `test_nexhealth_appointment_webhook` — so this fails when a PMS changes shape,
not only when we change our own code.
"""

from __future__ import annotations

import pytest

from src.app.services.automation.canonical_context import (
    appointment_event_key,
    merge_canonical_context,
)
from src.app.services.automation.event_catalog import (
    ALL_EVENT_KEYS,
    EVENTS,
    context_fields,
    supports,
)
from src.app.services.automation.filter_expression import context_value

# --- representative payloads -------------------------------------------------

# NexHealth's appointment webhook carries ten fields. Everything else a campaign
# needs is joined locally or synthesized, which is exactly why this fixture is
# deliberately this thin.
NEXHEALTH_APPOINTMENT = {
    "event": "appointment_updated",
    "pms_source": "nexhealth",
    "source": "nexhealth",
    "nexhealth_appointment_id": "9001",
    "appointment_id": "9001",
    "nexhealth_location_id": "loc-1",
    "location_id": "loc-1",
    "nexhealth_patient_id": "p-77",
    "contact_id": "c-77",
    "provider_id": "pr-3",
    "appointment_type_id": "at-4",
    "appointment_type_name": "Implant consult",
    "appointment_status": "booked",
    "appointment_reason": "implant surgery",
    "appointment_reasons": ["implant surgery"],
    "appointment_confirmed": False,
    "appointment_datetime": "2026-09-04T14:15:00",
    "patient_first_name": "Jordan",
    "patient_last_name": "Rivera",
}

# GoTracker sends roughly forty fields, PascalCase, with the date and the time of
# day split across two keys.
GOTRACKER_APPOINTMENT = {
    "event": "appointment.updated",
    "source": "gotracker",
    "gotracker_appointment_id": "1343",
    "appointment_id": "1343",
    "location_id": "loc-2",
    "gotracker_contact_id": "8821",
    "contact_id": "c-8821",
    "patient_first_name": "Jordan",
    "patient_last_name": "Rivera",
    "appointment_date": "2026-09-04T00:00:00.000Z",
    "appointment_time": "14:15:00",
    "appointment_duration": "00:15:00",
    "appointment_status_id": 1,
    "gotracker_status_id": 1,
    "appointment_reason": "implant surgery",
    "appointment_reasons": ["implant surgery"],
    "provider_id": "gt-2",
    "provider_name": "Dr Chan",
    "is_confirmed": False,
    "is_recall": False,
    "original_date": "2026-09-01T10:00:00",
    "flow_state": "Completed",
    "flow_changed_at": "2026-09-04T14:30:00",
}

PAYLOADS = {
    "nexhealth": NEXHEALTH_APPOINTMENT,
    "gotracker": GOTRACKER_APPOINTMENT,
}


def _project(payload: dict, event_key: str, pms: str) -> dict:
    return merge_canonical_context(
        dict(payload), event_key=event_key, source_pms=pms
    )


# --- the contract ------------------------------------------------------------


@pytest.mark.parametrize("pms", ["nexhealth", "gotracker"])
def test_appointment_events_produce_their_declared_context(pms: str) -> None:
    """Every declared appointment field either materialises or is unsupported.

    A field the catalog offers but the projection never writes is a trap: the
    author picks it, and the campaign silently never matches.
    """
    context = _project(PAYLOADS[pms], "appointment.cancelled", pms)

    missing: list[str] = []
    for field in context_fields("appointment.cancelled"):
        # `trigger.*` is stamped by the projection itself; location name and
        # timezone are resolved from our own records, not the PMS payload.
        if field.path.startswith(("trigger.", "location.name", "location.timezone")):
            continue
        if field.pms_support.get(pms) == "unsupported":
            continue
        if context_value(context, field.path) is None:
            missing.append(field.path)

    assert not missing, (
        f"{pms}: catalog declares {missing} for appointment.cancelled but the "
        f"projection never writes them. Either produce them or mark them "
        f"unsupported for {pms}."
    )


@pytest.mark.parametrize("pms", ["nexhealth", "gotracker"])
def test_the_canonical_status_agrees_across_both_systems(pms: str) -> None:
    """One campaign, one branch, whichever practice software is behind it."""
    context = _project(PAYLOADS[pms], "appointment.booked", pms)
    assert context_value(context, "appointment.status") == "booked"


def test_cancellation_resolves_to_one_event_on_both_systems() -> None:
    """NexHealth folds a flag into the status; GoTracker sends id 3."""
    nexhealth = {**NEXHEALTH_APPOINTMENT, "appointment_status": "cancelled"}
    gotracker = {
        **GOTRACKER_APPOINTMENT,
        "gotracker_status_id": 3,
        # Chair Flow completion outranks the status when inferring the event, so
        # a cancellation fixture must not also claim the visit finished.
        "flow_state": None,
    }

    assert appointment_event_key(nexhealth) == "appointment.cancelled"
    assert appointment_event_key(gotracker) == "appointment.cancelled"


def test_gotracker_split_date_and_time_become_one_instant() -> None:
    context = _project(GOTRACKER_APPOINTMENT, "appointment.booked", "gotracker")
    assert context_value(context, "appointment.start_at") == "2026-09-04T14:15:00"


def test_the_projection_never_overwrites_a_legacy_key() -> None:
    """Published definitions branch on the flat keys; they must not move."""
    context = _project(NEXHEALTH_APPOINTMENT, "appointment.booked", "nexhealth")
    for key, value in NEXHEALTH_APPOINTMENT.items():
        assert context[key] == value, f"projection clobbered legacy key {key}"


def test_absent_values_stay_absent_rather_than_becoming_empty() -> None:
    """The filter DSL treats missing as "cannot match"; "" would satisfy is_not_null."""
    sparse = {"source": "nexhealth", "appointment_id": "1", "appointment_reason": ""}
    context = _project(sparse, "appointment.booked", "nexhealth")
    assert context_value(context, "appointment.reason") is None


# --- the guards that stop the old failure modes returning ---------------------


def test_every_catalog_event_has_a_publisher() -> None:
    """An event nothing raises is a trigger that can never enroll anyone.

    Four keys were removed for exactly this reason. `TRIGGER_EVENT_KEYS` is the
    bridge from a dispatch path to the events it raises, so a key absent from it
    has no way to reach a workflow.
    """
    from src.app.services.automation.trigger_lookup import TRIGGER_EVENT_KEYS

    published = {key for keys in TRIGGER_EVENT_KEYS.values() for key in keys}
    orphans = sorted(set(ALL_EVENT_KEYS) - published)
    assert not orphans, (
        f"{orphans} are offered in the trigger picker but nothing raises them. "
        f"Wire a publisher or remove them from the catalog."
    )


def test_nexhealth_is_not_offered_events_it_cannot_detect() -> None:
    """A no-show is genuinely invisible on NexHealth — not merely derived.

    Its appointment is not cancelled, so the completion sweep marks it complete.
    Offering the trigger would build a campaign that can never fire.
    """
    assert supports("appointment.no_show", "nexhealth") == "unsupported"
    assert supports("appointment.no_show", "gotracker") == "native"


def test_every_event_declares_support_for_every_known_pms() -> None:
    for event in EVENTS:
        for pms in ("nexhealth", "gotracker"):
            assert pms in event.pms_support, (
                f"{event.key} does not say whether {pms} can raise it"
            )
