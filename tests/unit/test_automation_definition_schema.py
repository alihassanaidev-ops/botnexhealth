"""Unit tests for WorkflowDefinition Pydantic schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.services.automation.definition_schema import (
    WorkflowDefinition,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid definitions
# ---------------------------------------------------------------------------


def _sms_to_exit() -> dict:
    """Simplest valid definition: appointment_offset → send_sms → exit."""
    return {
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "entry_node_id": "sms-1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "Hi {{patient_name}}, reminder tomorrow.",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1", "outcome": "sent"},
        ],
    }


def _with_condition() -> dict:
    """Definition with a condition branch."""
    return {
        "trigger": {"type": "appointment_offset", "offset_hours": -48},
        "entry_node_id": "sms-1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "Please confirm your appointment.",
                "next_node_id": "cond-1",
            },
            {
                "type": "condition",
                "id": "cond-1",
                "logic": "AND",
                "rules": [{"field": "appointment_status", "op": "eq", "value": "confirmed"}],
                "true_next_node_id": "exit-confirmed",
                "false_next_node_id": "exit-unconfirmed",
            },
            {"type": "exit", "id": "exit-confirmed", "outcome": "confirmed"},
            {"type": "exit", "id": "exit-unconfirmed", "outcome": "no_response"},
        ],
    }


def _with_wait() -> dict:
    """Definition with a calendar-based wait node."""
    return {
        "trigger": {"type": "recall_scan", "recall_interval_months": 6},
        "entry_node_id": "wait-1",
        "nodes": [
            {
                "type": "wait",
                "id": "wait-1",
                "delay": {"delay_type": "calendar", "offset_days": 0, "time_of_day": "09:00"},
                "next_node_id": "sms-1",
            },
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "It's time for your check-up, {{patient_name}}.",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }


# ---------------------------------------------------------------------------
# Valid definitions
# ---------------------------------------------------------------------------


def test_minimal_sms_to_exit() -> None:
    d = WorkflowDefinition.model_validate(_sms_to_exit())
    assert d.schema_version == "1.0"
    assert d.entry_node_id == "sms-1"
    assert len(d.nodes) == 2


def test_condition_branch_definition() -> None:
    d = WorkflowDefinition.model_validate(_with_condition())
    assert len(d.nodes) == 4


def test_calendar_wait_definition() -> None:
    d = WorkflowDefinition.model_validate(_with_wait())
    wait = d.nodes[0]
    assert wait.type == "wait"
    assert wait.delay.delay_type == "calendar"
    assert wait.delay.time_of_day == "09:00"


def test_duration_wait() -> None:
    defn = _sms_to_exit()
    defn["entry_node_id"] = "wait-1"
    defn["nodes"].insert(0, {
        "type": "wait",
        "id": "wait-1",
        "delay": {"delay_type": "duration", "duration_seconds": 3600},
        "next_node_id": "sms-1",
    })
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].delay.duration_seconds == 3600


def test_voice_node() -> None:
    defn = {
        "trigger": {"type": "manual"},
        "entry_node_id": "voice-1",
        "nodes": [
            {
                "type": "send_voice",
                "id": "voice-1",
                "retell_agent_id": "agent-abc",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].retell_agent_id == "agent-abc"


def test_email_node() -> None:
    defn = {
        "trigger": {"type": "bulk_import"},
        "entry_node_id": "email-1",
        "nodes": [
            {
                "type": "send_email",
                "id": "email-1",
                "subject_template": "Your appointment",
                "body_template": "<p>Hi {{patient_name}}</p>",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].type == "send_email"


def test_condition_with_in_operator() -> None:
    defn = _with_condition()
    defn["nodes"][1]["rules"] = [
        {"field": "appointment_status", "op": "in", "value": ["confirmed", "pending"]}
    ]
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[1].rules[0].value == ["confirmed", "pending"]


def test_condition_is_null_operator_no_value() -> None:
    defn = _with_condition()
    defn["nodes"][1]["rules"] = [{"field": "phone", "op": "is_null"}]
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[1].rules[0].op == "is_null"


def test_max_attempts_on_action_node() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["max_attempts"] = 3
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].max_attempts == 3


def test_respect_quiet_hours_defaults_true() -> None:
    d = WorkflowDefinition.model_validate(_sms_to_exit())
    assert d.nodes[0].respect_quiet_hours is True


# ---------------------------------------------------------------------------
# Invalid definitions
# ---------------------------------------------------------------------------


def test_entry_node_id_not_in_nodes() -> None:
    defn = _sms_to_exit()
    defn["entry_node_id"] = "does-not-exist"
    with pytest.raises(ValidationError, match="entry_node_id"):
        WorkflowDefinition.model_validate(defn)


def test_next_node_id_references_missing_node() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["next_node_id"] = "ghost-node"
    with pytest.raises(ValidationError, match="next_node_id"):
        WorkflowDefinition.model_validate(defn)


def test_condition_true_branch_references_missing_node() -> None:
    defn = _with_condition()
    defn["nodes"][1]["true_next_node_id"] = "ghost-node"
    with pytest.raises(ValidationError, match="true_next_node_id"):
        WorkflowDefinition.model_validate(defn)


def test_condition_false_branch_references_missing_node() -> None:
    defn = _with_condition()
    defn["nodes"][1]["false_next_node_id"] = "ghost-node"
    with pytest.raises(ValidationError, match="false_next_node_id"):
        WorkflowDefinition.model_validate(defn)


def test_no_exit_node_raises() -> None:
    defn = _sms_to_exit()
    defn["nodes"] = [defn["nodes"][0]]
    defn["nodes"][0]["next_node_id"] = "sms-1"
    with pytest.raises(ValidationError, match="exit node"):
        WorkflowDefinition.model_validate(defn)


def test_empty_nodes_raises() -> None:
    defn = _sms_to_exit()
    defn["nodes"] = []
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_unknown_node_type_raises() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["type"] = "send_carrier_pigeon"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_unknown_trigger_type_raises() -> None:
    defn = _sms_to_exit()
    defn["trigger"]["type"] = "unknown_trigger"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_invalid_time_of_day_format_raises() -> None:
    defn = _with_wait()
    defn["nodes"][0]["delay"]["time_of_day"] = "9:00 AM"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_max_attempts_above_limit_raises() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["max_attempts"] = 10
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_extra_fields_rejected() -> None:
    defn = _sms_to_exit()
    defn["unexpected_field"] = "oops"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_empty_condition_rules_raises() -> None:
    defn = _with_condition()
    defn["nodes"][1]["rules"] = []
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_recall_interval_must_be_positive() -> None:
    defn = _with_wait()
    defn["trigger"]["recall_interval_months"] = 0
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)
