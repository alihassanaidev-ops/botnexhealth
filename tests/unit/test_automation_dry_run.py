"""Unit tests for the server-side dry-run simulator (Plan 02 B7)."""

from __future__ import annotations

from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.dry_run import simulate_run


def _defn(nodes: list, entry: str) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {"trigger": {"type": "manual"}, "entry_node_id": entry, "nodes": nodes}
    )


def test_dry_run_sms_to_exit_renders_sample_merge() -> None:
    d = _defn(
        [
            {"type": "send_sms", "id": "s1", "body_template": "Hi {{patient_first_name}}", "next_node_id": "x1"},
            {"type": "exit", "id": "x1", "outcome": "sent"},
        ],
        "s1",
    )
    r = simulate_run(d)
    assert r.outcome == "sent"
    assert [s.node_type for s in r.steps] == ["send_sms", "exit"]
    assert "Jordan" in (r.steps[0].detail or "")  # sample merge value rendered


def test_dry_run_renders_dental_sample_merge_fields() -> None:
    d = _defn(
        [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Visit {{appointment_date}} at {{appointment_time}} for {{appointment_reason}}",
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "sent"},
        ],
        "s1",
    )
    r = simulate_run(d)
    assert r.steps[0].detail == "Visit July 22, 2026 at 2:00 PM for bridge prep"


def test_dry_run_condition_follows_choice() -> None:
    d = _defn(
        [
            {
                "type": "condition",
                "id": "c1",
                "rules": [{"field": "x", "op": "eq", "value": "y"}],
                "true_next_node_id": "x1",
                "false_next_node_id": "x2",
            },
            {"type": "exit", "id": "x1", "outcome": "yes"},
            {"type": "exit", "id": "x2", "outcome": "no"},
        ],
        "c1",
    )
    assert simulate_run(d, condition_choices={"c1": False}).outcome == "no"
    assert simulate_run(d, condition_choices={"c1": True}).outcome == "yes"


def test_dry_run_describes_drip_action() -> None:
    d = _defn(
        [
            {
                "type": "drip",
                "id": "drip-1",
                "batch_size": 25,
                "interval_seconds": 3600,
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "released"},
        ],
        "drip-1",
    )
    r = simulate_run(d)
    assert [s.node_type for s in r.steps] == ["drip", "exit"]
    assert "25 contacts" in r.steps[0].summary


def test_dry_run_truncates_on_loop() -> None:
    d = _defn(
        [
            {"type": "wait", "id": "w1", "delay": {"delay_type": "duration", "duration_seconds": 1}, "next_node_id": "w1"},
            {"type": "exit", "id": "x1"},  # present (schema requires) but unreachable
        ],
        "w1",
    )
    r = simulate_run(d)
    assert r.truncated is True


def test_dry_run_supports_internal_status_and_pms_appointment_updates() -> None:
    definition = _defn(
        [
            {
                "type": "update_patient_status",
                "id": "status-1",
                "status": "ready_to_book",
                "next_node_id": "appointment-1",
            },
            {
                "type": "update_appointment",
                "id": "appointment-1",
                "operation": "confirm",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1", "outcome": "confirmed"},
        ],
        "status-1",
    )

    result = simulate_run(definition)

    assert [step.node_type for step in result.steps] == [
        "update_patient_status",
        "update_appointment",
        "exit",
    ]
