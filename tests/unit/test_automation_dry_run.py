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
