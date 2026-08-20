"""PMS parity between the NexHealth and GoTracker workflow paths.

Covers developer-task-list items 1.2 (NexHealth trigger metadata) and 1.1
(PMS-neutral appointment write-back).
"""

from __future__ import annotations

import pytest

from src.app.api.routes.nexhealth_webhooks import _appointment_trigger_metadata
from src.app.services.automation.campaign_templates import TEMPLATES
from src.app.services.automation.definition_schema import UpdateAppointmentNode


# The fields the shipped confirmation template's entry condition reads. If a
# template starts branching on something else, this list must grow with it.
TEMPLATE_ENTRY_FIELDS = ("appointment_status", "appointment_reason")


def _metadata(**overrides):
    kwargs = {
        "event": "appointment_created",
        "appt": {"confirmed": False},
        "appointment_id": "appt-1",
        "nexhealth_location_id": "loc-9",
        "nexhealth_patient_id": "pat-2",
        "provider_id": "prov-3",
        "appointment_type_id": "type-4",
        "appointment_reason": "Implant Surgery",
        "start_time": "2026-09-01T15:00:00Z",
        "cancelled": False,
    }
    kwargs.update(overrides)
    return _appointment_trigger_metadata(**kwargs)


# ── Item 1.2 · trigger metadata ───────────────────────────────────────────


def test_nexhealth_metadata_supplies_every_field_the_template_reads() -> None:
    """The entry condition used to evaluate against missing context and exit."""
    metadata = _metadata()
    for field in TEMPLATE_ENTRY_FIELDS:
        assert field in metadata, f"{field} missing from NexHealth trigger metadata"
        assert metadata[field] is not None


def test_nexhealth_status_matches_the_gotracker_label_vocabulary() -> None:
    """GoTracker status id 1 renders as "booked"; NexHealth must agree."""
    from src.app.api.routes.gotracker_webhooks import _gotracker_status_label

    assert _metadata()["appointment_status"] == _gotracker_status_label("1")


def test_nexhealth_cancelled_appointment_reports_cancelled_status() -> None:
    assert _metadata(cancelled=True)["appointment_status"] == "cancelled"


def test_template_entry_condition_passes_on_nexhealth_metadata() -> None:
    """End-to-end on the rule set: the live template must be eligible."""
    template = TEMPLATES["surgery-pre-appointment-confirmation"]
    nodes = {node["id"]: node for node in template.definition["nodes"]}
    rules = nodes["check-eligible-reason"]["rules"]

    metadata = _metadata()
    status_rule = next(r for r in rules if r["field"] == "appointment_status")
    assert metadata["appointment_status"] in status_rule["value"]

    # The reason rule carries a placeholder until setup substitutes real values,
    # so assert the field is populated and comparable rather than its content.
    reason_rule = next(r for r in rules if r["field"] == "appointment_reason")
    assert reason_rule["op"] == "in_case_insensitive"
    assert isinstance(metadata["appointment_reason"], str)


def test_unresolved_appointment_type_is_explicit_not_silent() -> None:
    """A missing reason label is None and empty-list, never a fabricated value."""
    metadata = _metadata(appointment_reason=None)
    assert metadata["appointment_reason"] is None
    assert metadata["appointment_reasons"] == []


def test_nexhealth_metadata_keeps_the_legacy_identifier_fields() -> None:
    """Existing consumers of the old three-field payload must not break."""
    metadata = _metadata()
    assert metadata["event"] == "appointment_created"
    assert metadata["nexhealth_appointment_id"] == "appt-1"
    assert metadata["nexhealth_location_id"] == "loc-9"


def test_confirmed_flag_is_tri_state() -> None:
    """None means "NexHealth did not say", which differs from False."""
    assert _metadata(appt={"confirmed": True})["appointment_confirmed"] is True
    assert _metadata(appt={"confirmed": False})["appointment_confirmed"] is False
    assert _metadata(appt={})["appointment_confirmed"] is None


# ── Item 1.1 · PMS-neutral write-back node ────────────────────────────────


@pytest.mark.parametrize("operation", ["confirm", "cancel", "reschedule"])
def test_neutral_node_accepts_every_operation(operation: str) -> None:
    kwargs = {"start_time": "{{reschedule_start_time}}"} if operation == "reschedule" else {}
    node = UpdateAppointmentNode(
        id="write", next_node_id="exit", operation=operation, **kwargs
    )
    assert node.type == "update_appointment"
    assert node.operation == operation


def test_reschedule_without_start_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="start_time"):
        UpdateAppointmentNode(id="write", next_node_id="exit", operation="reschedule")


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(ValueError):
        UpdateAppointmentNode(id="write", next_node_id="exit", operation="delete")


def test_neutral_node_is_in_the_workflow_node_union() -> None:
    """A definition using the node must survive schema validation."""
    from src.app.services.automation.definition_schema import WorkflowDefinition

    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "appointment_offset", "offset_hours": -24},
            "entry_node_id": "write",
            "nodes": [
                {
                    "type": "update_appointment",
                    "id": "write",
                    "operation": "confirm",
                    "next_node_id": "done",
                },
                {"type": "exit", "id": "done", "outcome": "confirmed"},
            ],
        }
    )
    assert definition.nodes[0].type == "update_appointment"


def test_live_template_uses_the_neutral_node_everywhere() -> None:
    """Item 1.1 acceptance: no GoTracker-only write-back left in the template."""
    template = TEMPLATES["surgery-pre-appointment-confirmation"]
    types = {node["type"] for node in template.definition["nodes"]}
    assert "update_appointment" in types
    assert "update_gotracker_appointment" not in types


def test_gotracker_translation_preserves_previous_behaviour() -> None:
    """The neutral node must map onto exactly what the template used to declare."""
    from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher

    translate = WorkflowStepDispatcher._gotracker_node_for

    confirmed = translate(
        None, UpdateAppointmentNode(id="c", next_node_id="n", operation="confirm")
    )
    assert confirmed.confirmed is True
    assert confirmed.preconfirmed is None

    cancelled = translate(
        None, UpdateAppointmentNode(id="x", next_node_id="n", operation="cancel")
    )
    assert cancelled.status_id == 3

    rescheduled = translate(
        None,
        UpdateAppointmentNode(
            id="r",
            next_node_id="n",
            operation="reschedule",
            start_time="{{reschedule_start_time}}",
        ),
    )
    assert rescheduled.start_time == "{{reschedule_start_time}}"


def test_translation_keeps_node_identity() -> None:
    """Step ids and edges must survive translation or the graph breaks."""
    from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher

    node = UpdateAppointmentNode(id="write-x", next_node_id="exit-y", operation="confirm")
    translated = WorkflowStepDispatcher._gotracker_node_for(None, node)
    assert translated.id == "write-x"
    assert translated.next_node_id == "exit-y"
