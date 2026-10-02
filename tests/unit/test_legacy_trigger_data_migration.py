"""The data migration that rewrites stored triggers onto the current vocabulary.

Campaigns published before the rearchitecture kept running, because the loader
converts their JSON on read. But the builder is handed that raw JSON, so the
trigger picker had no entry for the campaign's own trigger — opening one and
touching the dropdown silently replaced it.

What must hold: a rewritten definition loads to exactly what the read-time
converter would have produced from the original. If those ever disagree, a
campaign would behave differently after the migration than before it.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from src.app.services.automation.definition_schema import WorkflowDefinition

_SPEC = importlib.util.spec_from_file_location(
    "_legacy_trigger_migration",
    "alembic/versions/20260907_migrate_legacy_triggers.py",
)
migration = importlib.util.module_from_spec(_SPEC)
sys.modules["_legacy_trigger_migration"] = migration
_SPEC.loader.exec_module(migration)

NODES = [{"id": "n", "type": "exit", "outcome": "done"}]


def _definition(trigger: dict) -> dict:
    return {
        "schema_version": "1.0",
        "trigger": trigger,
        "entry_node_id": "n",
        "nodes": NODES,
    }


LEGACY_TRIGGERS = [
    {"type": "appointment_offset", "offset_hours": -24},
    {"type": "appointment_offset", "offset_hours": 2},
    {"type": "appointment_state_changed", "flow_states": ["Completed"],
     "max_followup_delay_hours": 72, "campaign_goal": "post_op_followup"},
    {"type": "appointment_state_changed", "status_ids": [3]},
    {"type": "appointment_state_changed", "confirmed": True},
    {"type": "recall_scan", "recall_interval_months": 18,
     "recall_reenrollment_cooldown_days": 120},
    {"type": "bulk_import"},
    {"type": "enquiry_received"},
    {"type": "callback_requested"},
    {"type": "patient_status_changed", "statuses": ["post_op_done"]},
    {"type": "sms_reply", "tokens": ["YES"]},
    {"type": "email_reply", "tokens": ["YES"]},
]

CURRENT_TYPES = {
    "event", "manual", "form_submitted",
    "internal_status", "schedule", "inbound_message",
}


@pytest.mark.parametrize("trigger", LEGACY_TRIGGERS, ids=lambda t: t["type"])
def test_a_rewritten_definition_behaves_exactly_as_before(trigger: dict) -> None:
    """The migration must not change how a published campaign runs."""
    before = _definition(trigger)
    after = migration._rewrite(before)
    assert after is not None, f"{trigger['type']} was left unmigrated"

    assert (
        WorkflowDefinition.model_validate(after).model_dump(mode="json")
        == WorkflowDefinition.model_validate(before).model_dump(mode="json")
    )


@pytest.mark.parametrize("trigger", LEGACY_TRIGGERS, ids=lambda t: t["type"])
def test_the_result_is_a_trigger_the_builder_can_offer(trigger: dict) -> None:
    """The whole point: the picker must have an entry for it afterwards."""
    after = migration._rewrite(_definition(trigger))
    assert after["triggers"][0]["type"] in CURRENT_TYPES
    assert "trigger" not in after


def test_an_eligibility_filter_survives_the_rewrite() -> None:
    """The surgery campaigns gate on appointment status; losing that would
    enroll every appointment in the clinic."""
    rule = {
        "kind": "rule",
        "field": "appointment_status",
        "op": "in_case_insensitive",
        "value": ["booked"],
    }
    after = migration._rewrite(
        _definition({"type": "appointment_offset", "offset_hours": -24, "filter": rule})
    )
    assert after["triggers"][0]["filter"] == rule


def test_the_callback_classification_becomes_an_explicit_filter() -> None:
    after = migration._rewrite(_definition({"type": "callback_requested"}))
    assert after["triggers"][0]["filter"]["field"] == "call.outcome"
    assert after["triggers"][0]["filter"]["value"] == "needs_callback"


def test_a_definition_already_on_the_new_shape_is_left_alone() -> None:
    """Re-running the migration must be a no-op, not a second rewrite."""
    current = {
        "schema_version": "1.0",
        "triggers": [{"type": "event", "event_keys": ["appointment.cancelled"]}],
        "entry_node_id": "n",
        "nodes": NODES,
    }
    assert migration._rewrite(current) is None


def test_rewriting_twice_changes_nothing_the_second_time() -> None:
    once = migration._rewrite(_definition({"type": "recall_scan", "recall_interval_months": 6}))
    assert migration._rewrite(once) is None
