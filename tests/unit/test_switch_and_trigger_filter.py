"""Switch routing, the condition node's two authoring shapes, and trigger filters."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.app.services.automation.definition_schema import (
    ConditionNode,
    SwitchNode,
    WorkflowDefinition,
)
from src.app.services.automation.dry_run import simulate_run
from src.app.services.automation.node_registry import outgoing_references
from src.app.services.automation.step_dispatcher import (
    evaluate_condition_node,
    select_switch_case,
)
from src.app.services.automation.trigger_filter import trigger_filter_matches

_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _switch(**overrides) -> SwitchNode:
    payload = {
        "type": "switch",
        "id": "route",
        "subject": "call_outcome",
        "cases": [
            {
                "label": "Confirmed",
                "filter": {"kind": "rule", "field": "call_outcome", "op": "eq", "value": "confirmed"},
                "next_node_id": "n-confirmed",
            },
            {
                "label": "Unreachable",
                "filter": {
                    "kind": "rule",
                    "field": "call_outcome",
                    "op": "in_case_insensitive",
                    "value": ["no_answer", "voicemail", "busy"],
                },
                "next_node_id": "n-unreachable",
            },
        ],
        "default_next_node_id": "n-review",
    }
    payload.update(overrides)
    return SwitchNode.model_validate(payload)


# ---------------------------------------------------------------------------
# Switch routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected_label"),
    [
        ("confirmed", "Confirmed"),
        ("no_answer", "Unreachable"),
        ("VOICEMAIL", "Unreachable"),
        ("something_new", None),
        (None, None),
    ],
)
def test_switch_routes_to_the_first_matching_case(outcome, expected_label) -> None:
    matched = select_switch_case(_switch(), {"call_outcome": outcome}, now=_NOW)
    assert (matched.label if matched else None) == expected_label


def test_switch_cases_are_ordered_so_the_specific_one_can_win() -> None:
    node = _switch(
        cases=[
            {
                "label": "Specific",
                "filter": {"kind": "rule", "field": "v", "op": "eq", "value": "abc"},
                "next_node_id": "n1",
            },
            {
                "label": "General",
                "filter": {"kind": "rule", "field": "v", "op": "contains", "value": "a"},
                "next_node_id": "n2",
            },
        ]
    )
    assert select_switch_case(node, {"v": "abc"}, now=_NOW).label == "Specific"
    assert select_switch_case(node, {"v": "xax"}, now=_NOW).label == "General"


def test_switch_ports_are_reported_with_an_indexed_path() -> None:
    """A mis-wired case must point at the case, not at the node as a whole."""
    refs = dict(outgoing_references(_switch()))
    assert refs["cases[0].next_node_id"] == "n-confirmed"
    assert refs["cases[1].next_node_id"] == "n-unreachable"
    assert refs["default_next_node_id"] == "n-review"


def test_switch_rejects_duplicate_case_labels() -> None:
    """The label is the port identity in traces, so it has to be unique."""
    with pytest.raises(ValidationError):
        _switch(
            cases=[
                {
                    "label": "Same",
                    "filter": {"kind": "rule", "field": "v", "op": "is_null"},
                    "next_node_id": "n1",
                },
                {
                    "label": " same ",
                    "filter": {"kind": "rule", "field": "v", "op": "is_not_null"},
                    "next_node_id": "n2",
                },
            ]
        )


def test_switch_requires_a_default_branch() -> None:
    """An unrouted run would otherwise strand mid-graph."""
    with pytest.raises(ValidationError):
        SwitchNode.model_validate(
            {
                "type": "switch",
                "id": "route",
                "cases": [
                    {
                        "label": "A",
                        "filter": {"kind": "rule", "field": "v", "op": "is_null"},
                        "next_node_id": "n1",
                    }
                ],
            }
        )


def test_unreachable_switch_target_fails_graph_validation() -> None:
    with pytest.raises(ValidationError) as excinfo:
        WorkflowDefinition.model_validate(
            {
                "schema_version": "1.0",
                "trigger": {"type": "manual"},
                "entry_node_id": "route",
                "nodes": [
                    {
                        "type": "switch",
                        "id": "route",
                        "cases": [
                            {
                                "label": "A",
                                "filter": {"kind": "rule", "field": "v", "op": "is_null"},
                                "next_node_id": "does-not-exist",
                            }
                        ],
                        "default_next_node_id": "done",
                    },
                    {"type": "exit", "id": "done"},
                ],
            }
        )
    assert "cases[0].next_node_id" in str(excinfo.value)


# ---------------------------------------------------------------------------
# One switch replaces a chain of conditions
# ---------------------------------------------------------------------------


def test_switch_replaces_a_chain_of_binary_conditions() -> None:
    """Six outcomes cost one node instead of five chained conditions."""
    outcomes = ["confirmed", "cancelled", "reschedule", "callback", "do_not_call", "no_answer"]
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "route",
            "nodes": [
                {
                    "type": "switch",
                    "id": "route",
                    "subject": "call_outcome",
                    "cases": [
                        {
                            "label": outcome,
                            "filter": {
                                "kind": "rule",
                                "field": "call_outcome",
                                "op": "eq",
                                "value": outcome,
                            },
                            "next_node_id": f"exit-{outcome}",
                        }
                        for outcome in outcomes
                    ],
                    "default_next_node_id": "exit-review",
                },
                *[
                    {"type": "exit", "id": f"exit-{outcome}", "outcome": outcome}
                    for outcome in outcomes
                ],
                {"type": "exit", "id": "exit-review", "outcome": "staff_handoff"},
            ],
        }
    )
    node = definition.nodes[0]
    assert len([n for n in definition.nodes if n.type == "switch"]) == 1

    for outcome in outcomes:
        assert select_switch_case(node, {"call_outcome": outcome}, now=_NOW).label == outcome
    assert select_switch_case(node, {"call_outcome": "novel"}, now=_NOW) is None


def test_dry_run_walks_a_named_switch_branch() -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "route",
            "nodes": [
                {
                    "type": "switch",
                    "id": "route",
                    "cases": [
                        {
                            "label": "Confirmed",
                            "filter": {
                                "kind": "rule",
                                "field": "call_outcome",
                                "op": "eq",
                                "value": "confirmed",
                            },
                            "next_node_id": "yes",
                        }
                    ],
                    "default_next_node_id": "no",
                },
                {"type": "exit", "id": "yes", "outcome": "confirmed"},
                {"type": "exit", "id": "no", "outcome": "other"},
            ],
        }
    )
    assert simulate_run(definition).outcome == "other"
    assert (
        simulate_run(definition, switch_case_choices={"route": "Confirmed"}).outcome
        == "confirmed"
    )


# ---------------------------------------------------------------------------
# Condition node: both shapes, and the legacy one left alone
# ---------------------------------------------------------------------------


def test_condition_accepts_a_filter() -> None:
    node = ConditionNode.model_validate(
        {
            "type": "condition",
            "id": "c",
            "filter": {
                "kind": "group",
                "op": "or",
                "children": [
                    {"kind": "rule", "field": "amount", "op": "gt", "value": 2000},
                    {"kind": "rule", "field": "vip", "op": "eq", "value": True},
                ],
            },
            "true_next_node_id": "a",
            "false_next_node_id": "b",
        }
    )
    assert evaluate_condition_node(node, {"amount": "2400"}, now=_NOW) is True
    assert evaluate_condition_node(node, {"amount": "10", "vip": True}, now=_NOW) is True
    assert evaluate_condition_node(node, {"amount": "10"}, now=_NOW) is False


def test_legacy_rules_keep_exact_equality() -> None:
    """The old evaluator does not coerce; rewriting it could change live branching."""
    node = ConditionNode.model_validate(
        {
            "type": "condition",
            "id": "c",
            "rules": [{"field": "status_id", "op": "eq", "value": 1}],
            "true_next_node_id": "a",
            "false_next_node_id": "b",
        }
    )
    # The string "1" is NOT equal to 1 under the legacy evaluator, and that is
    # the behaviour published definitions were authored against.
    assert evaluate_condition_node(node, {"status_id": "1"}, now=_NOW) is False
    assert evaluate_condition_node(node, {"status_id": 1}, now=_NOW) is True

    # The same test written as a filter does coerce, which is the new behaviour
    # an author opts into by using the new shape.
    modern = ConditionNode.model_validate(
        {
            "type": "condition",
            "id": "c",
            "filter": {"kind": "rule", "field": "status_id", "op": "eq", "value": 1},
            "true_next_node_id": "a",
            "false_next_node_id": "b",
        }
    )
    assert evaluate_condition_node(modern, {"status_id": "1"}, now=_NOW) is True


def test_condition_requires_exactly_one_shape() -> None:
    base = {
        "type": "condition",
        "id": "c",
        "true_next_node_id": "a",
        "false_next_node_id": "b",
    }
    with pytest.raises(ValidationError):
        ConditionNode.model_validate(base)
    with pytest.raises(ValidationError):
        ConditionNode.model_validate(
            {
                **base,
                "rules": [{"field": "x", "op": "eq", "value": 1}],
                "filter": {"kind": "rule", "field": "x", "op": "is_null"},
            }
        )


# ---------------------------------------------------------------------------
# Trigger filters
# ---------------------------------------------------------------------------


def _workflow(trigger: dict) -> MagicMock:
    workflow = MagicMock()
    workflow.id = "wf-1"
    workflow.definition = {
        "schema_version": "1.0",
        "trigger": trigger,
        "entry_node_id": "e",
        "nodes": [{"type": "exit", "id": "e"}],
    }
    return workflow


def test_trigger_without_a_filter_always_matches() -> None:
    """Every definition published before filters existed."""
    assert trigger_filter_matches(_workflow({"type": "appointment_offset", "offset_hours": -24}), {})


def test_trigger_filter_gates_enrollment() -> None:
    workflow = _workflow(
        {
            "type": "appointment_offset",
            "offset_hours": -24,
            "filter": {
                "kind": "rule",
                "field": "appointment_reason",
                "op": "in_case_insensitive",
                "value": ["implant surgery"],
            },
        }
    )
    assert trigger_filter_matches(workflow, {"appointment_reason": "Implant Surgery"})
    assert not trigger_filter_matches(workflow, {"appointment_reason": "cleaning"})
    # A missing field must not enroll.
    assert not trigger_filter_matches(workflow, {})


def test_unparseable_definition_does_not_match() -> None:
    workflow = MagicMock()
    workflow.id = "wf-broken"
    workflow.definition = {"nonsense": True}
    assert trigger_filter_matches(workflow, {}) is False


def test_workflow_without_a_definition_does_not_match() -> None:
    workflow = MagicMock()
    workflow.id = "wf-draft"
    workflow.definition = None
    assert trigger_filter_matches(workflow, {}) is False
