"""What caused each write into a practice's records (Item 34).

The acceptance criterion that matters is the third one: an operator
investigating a duplicate or unexpected booking can trace it back to the
campaign run responsible *without reading source code*. That rules out a
correct-but-invisible implementation, so these tests check the trail as well as
the storage — the fields exist, they survive into the payload sent across the
boundary, and the distinctions they draw are ones an investigation needs.
"""

from __future__ import annotations

import structlog

from src.app.pms.models import BookingRequest
from src.app.services.write_provenance import (
    WriteActor,
    WriteProvenance,
    current_trace_id,
)


# ── The distinction the item exists to draw ──────────────────────────


def test_a_patient_link_booking_is_not_recorded_as_a_campaign() -> None:
    """Both carry a run id, and that is exactly why actor is needed.

    The campaign sent the link; the patient chose the slot. An investigation
    into an unexpected booking has to know which of those to look at, and the
    run id alone cannot say.
    """
    campaign = WriteProvenance.for_campaign(workflow_run_id="run-1", step_id="node-1")
    patient = WriteProvenance.for_patient_link(workflow_run_id="run-1")

    assert campaign.workflow_run_id == patient.workflow_run_id
    assert campaign.actor is WriteActor.CAMPAIGN
    assert patient.actor is WriteActor.PATIENT_LINK


def test_a_campaign_write_records_run_and_step() -> None:
    provenance = WriteProvenance.for_campaign(
        workflow_run_id="run-1", step_id="node-7"
    )
    assert provenance.workflow_run_id == "run-1"
    assert provenance.step_id == "node-7"


def test_a_system_write_can_carry_a_stated_reason() -> None:
    """The override case: an operator forcing something needs to say why."""
    provenance = WriteProvenance.for_system(reason="operator resolved conflict #42")
    assert provenance.actor is WriteActor.SYSTEM
    assert provenance.reason == "operator resolved conflict #42"


# ── The trace identifier ─────────────────────────────────────────────


def test_the_trace_id_is_the_one_following_the_interaction() -> None:
    """Picked up from the logging context rather than threaded by hand.

    Threading it through every call site is how the run id came to be set on
    some write paths and not others.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc")
    try:
        assert current_trace_id() == "req-abc"
        assert (
            WriteProvenance.for_campaign(
                workflow_run_id="run-1", step_id="n1"
            ).trace_id
            == "req-abc"
        )
    finally:
        structlog.contextvars.clear_contextvars()


def test_a_trace_id_is_never_empty() -> None:
    """A write with no trace is the thing this item exists to prevent.

    Outside a request — a worker that bound no context — a fresh id still ties
    this write to whatever else happens under it, which beats a row nobody can
    follow at all.
    """
    structlog.contextvars.clear_contextvars()
    first = current_trace_id()
    second = current_trace_id()

    assert first and second
    # Distinct, because they are genuinely different unrelated interactions.
    assert first != second


def test_an_explicit_trace_id_wins_over_the_ambient_one() -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="ambient")
    try:
        provenance = WriteProvenance.for_campaign(
            workflow_run_id="run-1", step_id="n1", trace_id="explicit"
        )
        assert provenance.trace_id == "explicit"
    finally:
        structlog.contextvars.clear_contextvars()


# ── Surviving the boundary ───────────────────────────────────────────


def test_the_payload_carries_everything_an_investigation_needs() -> None:
    payload = WriteProvenance.for_campaign(
        workflow_run_id="run-1", step_id="node-2", trace_id="trace-9"
    ).as_payload()

    assert payload == {
        "actor": "campaign",
        "trace_id": "trace-9",
        "workflow_run_id": "run-1",
        "step_id": "node-2",
    }


def test_empty_fields_are_omitted_rather_than_sent_as_null() -> None:
    """So the receiving end can treat presence as meaning."""
    payload = WriteProvenance.for_system(trace_id="trace-9").as_payload()

    assert payload == {"actor": "system", "trace_id": "trace-9"}
    assert "workflow_run_id" not in payload
    assert "step_id" not in payload


def test_a_booking_request_can_carry_provenance() -> None:
    """The route the patient booking path takes into the Cloud Service."""
    booking = BookingRequest(
        patient_id="p-1",
        provider_id="prov-1",
        slot_start="2026-09-02T09:00:00Z",
        provenance=WriteProvenance.for_patient_link(
            workflow_run_id="run-1", trace_id="trace-9"
        ).as_payload(),
    )

    assert booking.provenance is not None
    assert booking.provenance["actor"] == "patient_link"
    assert booking.provenance["trace_id"] == "trace-9"


def test_a_booking_request_without_provenance_still_validates() -> None:
    """An unconverted caller books rather than failing."""
    booking = BookingRequest(
        patient_id="p-1", provider_id="prov-1", slot_start="2026-09-02T09:00:00Z"
    )
    assert booking.provenance is None


# ── Failing honestly ─────────────────────────────────────────────────


def test_actor_values_are_stable_strings() -> None:
    """They are stored and shown to operators; renaming orphans history."""
    assert WriteActor.CAMPAIGN.value == "campaign"
    assert WriteActor.PATIENT_LINK.value == "patient_link"
    assert WriteActor.VOICE_AGENT.value == "voice_agent"
    assert WriteActor.STAFF.value == "staff"
    assert WriteActor.SYSTEM.value == "system"


def test_provenance_is_immutable() -> None:
    """A cause that can be edited after the fact is not a record of anything."""
    import dataclasses

    import pytest

    provenance = WriteProvenance.for_campaign(workflow_run_id="run-1", step_id="n1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        provenance.actor = WriteActor.STAFF  # type: ignore[misc]
