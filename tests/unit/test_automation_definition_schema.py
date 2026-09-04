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
                "rules": [
                    {"field": "appointment_status", "op": "eq", "value": "confirmed"}
                ],
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
        "trigger": {
            "type": "schedule",
            "cron": "0 9 * * *",
            "source": {"kind": "pms_recall", "recall_interval_months": 6},
        },
        "entry_node_id": "wait-1",
        "nodes": [
            {
                "type": "wait",
                "id": "wait-1",
                "delay": {
                    "delay_type": "calendar",
                    "offset_days": 0,
                    "time_of_day": "09:00",
                },
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
    assert d.nodes[0].expect_response is False
    assert d.nodes[0].include_opt_out_footer is True
    assert d.nodes[0].response_window_seconds == 72 * 60 * 60
    assert d.nodes[0].response_mappings == []


def test_pms_context_fields_are_trimmed_and_deduped() -> None:
    definition = _sms_to_exit()
    definition["pms_context_fields"] = [
        " recall_type_name ",
        "has_active_treatment_plan",
        "recall_type_name",
    ]

    parsed = WorkflowDefinition.model_validate(definition)

    assert parsed.pms_context_fields == [
        "recall_type_name",
        "has_active_treatment_plan",
    ]


def test_pms_context_fields_reject_unknown_fields() -> None:
    definition = _sms_to_exit()
    definition["pms_context_fields"] = ["clinical_note_body"]

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(definition)


def test_sms_node_can_disable_automatic_opt_out_footer() -> None:
    definition = _sms_to_exit()
    definition["nodes"][0]["include_opt_out_footer"] = False

    parsed = WorkflowDefinition.model_validate(definition)

    assert parsed.nodes[0].include_opt_out_footer is False


def test_legacy_sms_reply_key_is_ignored() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0].update(
        {
            "expect_response": True,
            "include_reply_key": True,
            "response_window_seconds": 3600,
            "response_mappings": [
                {
                    "tokens": ["YES", "confirm"],
                    "context_updates": {"appointment_status": "confirmed"},
                }
            ],
        }
    )

    d = WorkflowDefinition.model_validate(defn)
    sms = d.nodes[0]
    assert sms.expect_response is True
    assert "include_reply_key" not in sms.model_dump()
    assert sms.response_mappings[0].tokens == ["YES", "confirm"]


def test_legacy_wait_for_sms_reply_node_still_validates() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["next_node_id"] = "wait-reply"
    defn["nodes"].insert(
        1,
        {
            "type": "wait_for_sms_reply",
            "id": "wait-reply",
            "next_node_id": "exit-1",
            "include_reply_key": True,
            "response_window_seconds": 3600,
            "response_mappings": [
                {
                    "tokens": ["YES", "Y"],
                    "context_updates": {"sms_reply": "yes"},
                }
            ],
        },
    )

    d = WorkflowDefinition.model_validate(defn)
    wait = d.nodes[1]
    assert wait.type == "wait_for_sms_reply"
    assert "include_reply_key" not in wait.model_dump()
    assert wait.response_mappings[0].tokens == ["YES", "Y"]


def test_unified_sms_reply_wait_validates() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["next_node_id"] = "wait-reply"
    defn["nodes"].insert(
        1,
        {
            "type": "wait",
            "id": "wait-reply",
            "next_node_id": "exit-1",
            "wait_for": {
                "type": "sms_reply",
                "include_reply_key": True,
                "response_window_seconds": 3600,
                "response_mappings": [
                    {
                        "tokens": ["YES", "Y"],
                        "context_updates": {"sms_reply": "yes"},
                    }
                ],
            },
        },
    )

    wait = WorkflowDefinition.model_validate(defn).nodes[1]

    assert wait.type == "wait"
    assert wait.wait_for.type == "sms_reply"
    assert "include_reply_key" not in wait.wait_for.model_dump()
    assert wait.wait_for.response_mappings[0].tokens == ["YES", "Y"]


def test_inbound_message_trigger_validates() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "inbound_message",
        "channels": ["sms"],
        "tokens": ["pricing", "Pricing", "reschedule"],
        "campaign_goal": "inbound_sms_followup",
    }

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.type == "inbound_message"
    assert d.trigger.channels == ["sms"]
    assert d.trigger.tokens == ["pricing", "reschedule"]


def test_inbound_message_trigger_serves_both_channels() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "inbound_message", "channels": ["sms", "email"]}

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.channels == ["sms", "email"]


def test_legacy_sms_reply_upconverts_to_inbound_message() -> None:
    """A published sms_reply definition keeps running after the rename."""
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "sms_reply", "tokens": ["YES"]}

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.type == "inbound_message"
    assert d.trigger.channels == ["sms"]
    assert d.trigger.tokens == ["YES"]


def test_legacy_email_reply_upconverts_to_the_email_channel() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "email_reply", "tokens": ["YES"]}

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.type == "inbound_message"
    assert d.trigger.channels == ["email"]


def test_enquiry_event_trigger_validates_with_filter() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "event",
        "event_keys": ["enquiry.received"],
        "filter": {
            "kind": "rule",
            "field": "enquiry.source",
            "op": "eq",
            "value": "website_form",
        },
    }

    d = WorkflowDefinition.model_validate(defn)

    assert d.trigger.type == "event"
    assert d.trigger.event_keys == ["enquiry.received"]
    assert d.trigger.filter is not None


def test_event_trigger_rejects_an_unknown_event_key() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "event", "event_keys": ["appointment.exploded"]}

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_event_trigger_dedupes_and_preserves_key_order() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "event",
        "event_keys": ["appointment.cancelled", "appointment.no_show", "appointment.cancelled"],
    }

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.event_keys == ["appointment.cancelled", "appointment.no_show"]


def test_reminder_offset_is_required_for_the_reminder_event() -> None:
    """The reminder event is defined by its interval, so it cannot go unset."""
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "event", "event_keys": ["appointment.reminder_due"]}

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_reminder_offset_is_rejected_on_other_events() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "event",
        "event_keys": ["appointment.cancelled"],
        "reminder_offset_hours": -24,
    }

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_a_workflow_can_start_from_several_triggers() -> None:
    defn = _sms_to_exit()
    defn.pop("trigger")
    defn["triggers"] = [
        {"type": "event", "event_keys": ["appointment.cancelled"]},
        {"type": "manual"},
    ]

    d = WorkflowDefinition.model_validate(defn)

    assert [t.type for t in d.triggers] == ["event", "manual"]
    # `.trigger` keeps the single-entry-point call sites working.
    assert d.trigger.type == "event"


def test_condition_branch_definition() -> None:
    d = WorkflowDefinition.model_validate(_with_condition())
    assert len(d.nodes) == 4


def test_calendar_wait_definition() -> None:
    d = WorkflowDefinition.model_validate(_with_wait())
    wait = d.nodes[0]
    assert wait.type == "wait"
    assert wait.wait_for.type == "time"
    assert wait.wait_for.delay.delay_type == "calendar"
    assert wait.wait_for.delay.time_of_day == "09:00"


def test_legacy_duration_wait_is_upgraded() -> None:
    defn = _sms_to_exit()
    defn["entry_node_id"] = "wait-1"
    defn["nodes"].insert(
        0,
        {
            "type": "wait",
            "id": "wait-1",
            "delay": {"delay_type": "duration", "duration_seconds": 3600},
            "next_node_id": "sms-1",
        },
    )
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].wait_for.type == "time"
    assert d.nodes[0].wait_for.delay.duration_seconds == 3600


def test_drip_node() -> None:
    defn = {
        "trigger": {"type": "manual"},
        "entry_node_id": "drip-1",
        "nodes": [
            {
                "type": "drip",
                "id": "drip-1",
                "batch_size": 25,
                "interval_seconds": 3600,
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].type == "drip"
    assert d.nodes[0].batch_size == 25


def test_drip_node_rejects_invalid_batch_settings() -> None:
    defn = {
        "trigger": {"type": "manual"},
        "entry_node_id": "drip-1",
        "nodes": [
            {
                "type": "drip",
                "id": "drip-1",
                "batch_size": 0,
                "interval_seconds": 0,
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


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
                "phone_country_code_enabled": True,
                "phone_country_region": "GB",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].retell_agent_id == "agent-abc"
    assert d.nodes[0].phone_country_code_enabled is True
    assert d.nodes[0].phone_country_region == "GB"


def test_voice_node_rejects_invalid_phone_country_region() -> None:
    defn = {
        "trigger": {"type": "manual"},
        "entry_node_id": "voice-1",
        "nodes": [
            {
                "type": "send_voice",
                "id": "voice-1",
                "retell_agent_id": "agent-abc",
                "next_node_id": "exit-1",
                "phone_country_code_enabled": True,
                "phone_country_region": "USA",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


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


def test_json_mapper_and_llm_nodes() -> None:
    defn = {
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "entry_node_id": "map-1",
        "nodes": [
            {
                "type": "json_mapper",
                "id": "map-1",
                "mappings": [
                    {
                        "source_path": "gotracker_payload.appointment.reasons",
                        "target_field": "appointment_reasons",
                    }
                ],
                "next_node_id": "llm-1",
            },
            {
                "type": "llm",
                "id": "llm-1",
                "source_field": "appointment_reasons",
                "output_field": "appointment_category",
                "prompt_template": "Classify the appointment reason.",
                "labels": ["implant", "hygiene"],
                "label_rules": [
                    {"label": "implant", "keywords": ["implant", "surgery"]}
                ],
                "fallback_label": "other",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }

    d = WorkflowDefinition.model_validate(defn)

    assert d.nodes[0].type == "json_mapper"
    assert d.nodes[1].type == "llm"


def test_max_attempts_on_action_node() -> None:
    defn = _sms_to_exit()
    defn["nodes"][0]["max_attempts"] = 3
    d = WorkflowDefinition.model_validate(defn)
    assert d.nodes[0].max_attempts == 3


def test_respect_quiet_hours_defaults_true() -> None:
    d = WorkflowDefinition.model_validate(_sms_to_exit())
    assert d.nodes[0].respect_quiet_hours is True


def test_internal_status_trigger() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "internal_status",
        "field": "contact_lead_status",
        "to_statuses": [" qualified "],
        "campaign_goal": " post_op_followup ",
    }

    d = WorkflowDefinition.model_validate(defn)

    assert d.trigger.type == "internal_status"
    assert d.trigger.field == "contact_lead_status"
    assert d.trigger.to_statuses == ["qualified"]
    assert d.trigger.from_statuses == []
    assert d.trigger.campaign_goal == "post_op_followup"


def test_internal_status_trigger_can_pin_the_transition() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "internal_status",
        "field": "call_workflow_status",
        "from_statuses": ["Pending"],
        "to_statuses": ["Completed"],
    }

    d = WorkflowDefinition.model_validate(defn)
    assert d.trigger.from_statuses == ["Pending"]
    assert d.trigger.to_statuses == ["Completed"]


def test_internal_status_trigger_rejects_an_unwatched_field() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "internal_status",
        "field": "call_ai_classification",
        "to_statuses": ["needs_callback"],
    }

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_legacy_patient_status_changed_upconverts() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {
        "type": "patient_status_changed",
        "statuses": ["appointment_confirmed"],
    }

    d = WorkflowDefinition.model_validate(defn)

    assert d.trigger.type == "internal_status"
    assert d.trigger.field == "patient_workflow_status"
    assert d.trigger.to_statuses == ["appointment_confirmed"]


def test_legacy_callback_requested_becomes_a_filtered_call_event() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "callback_requested"}

    d = WorkflowDefinition.model_validate(defn)

    assert d.trigger.type == "event"
    assert d.trigger.event_keys == ["call.inbound.completed"]
    # The classification the old trigger implied is now explicit.
    assert d.trigger.filter.field == "call.outcome"
    assert d.trigger.filter.value == "needs_callback"


def test_book_appointment_node_validates_three_outcome_branches() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "book-1",
        "nodes": [
            {
                "type": "book_appointment",
                "id": "book-1",
                "appointment_type_id": "{{appointment_type_id}}",
                "provider_id": "{{provider_id}}",
                "start_time": "{{booking_start_time}}",
                "booked_next_node_id": "booked",
                "could_not_book_next_node_id": "could-not-book",
                "pending_next_node_id": "pending",
            },
            {"type": "exit", "id": "booked", "outcome": "booked"},
            {"type": "exit", "id": "could-not-book", "outcome": "could_not_book"},
            {"type": "exit", "id": "pending", "outcome": "pending"},
        ],
    }

    parsed = WorkflowDefinition.model_validate(definition)

    node = parsed.nodes[0]
    assert node.type == "book_appointment"
    assert node.pending_next_node_id == "pending"


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


def test_update_appointment_next_node_is_validated_by_registry() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "update-1",
        "nodes": [
            {
                "type": "update_appointment",
                "id": "update-1",
                "operation": "confirm",
                "next_node_id": "ghost-node",
            },
            {"type": "exit", "id": "exit-1", "outcome": "done"},
        ],
    }

    with pytest.raises(ValidationError, match="update-1.*next_node_id.*ghost-node"):
        WorkflowDefinition.model_validate(definition)


def test_book_appointment_branches_are_validated_by_registry() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "book-1",
        "nodes": [
            {
                "type": "book_appointment",
                "id": "book-1",
                "appointment_type_id": "type-1",
                "provider_id": "provider-1",
                "start_time": "2026-09-02T14:30:00+00:00",
                "booked_next_node_id": "booked",
                "could_not_book_next_node_id": "ghost-node",
                "pending_next_node_id": "pending",
            },
            {"type": "exit", "id": "booked", "outcome": "booked"},
            {"type": "exit", "id": "pending", "outcome": "pending"},
        ],
    }

    with pytest.raises(
        ValidationError,
        match="book-1.*could_not_book_next_node_id.*ghost-node",
    ):
        WorkflowDefinition.model_validate(definition)


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


def test_gotracker_appointment_update_node_accepts_status_and_reschedule_fields() -> (
    None
):
    defn = _sms_to_exit()
    defn["entry_node_id"] = "gt-write"
    defn["nodes"] = [
        {
            "type": "update_gotracker_appointment",
            "id": "gt-write",
            "next_node_id": "exit-1",
            "status_id": 5,
            "start_time": "{{new_start_time}}",
            "duration_min": 45,
        },
        {"type": "exit", "id": "exit-1", "outcome": "updated"},
    ]

    parsed = WorkflowDefinition.model_validate(defn)

    assert parsed.nodes[0].type == "update_gotracker_appointment"


def test_gotracker_appointment_update_rejects_blank_update() -> None:
    defn = _sms_to_exit()
    defn["entry_node_id"] = "gt-write"
    defn["nodes"] = [
        {
            "type": "update_gotracker_appointment",
            "id": "gt-write",
            "next_node_id": "exit-1",
        },
        {"type": "exit", "id": "exit-1", "outcome": "updated"},
    ]
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_gotracker_appointment_update_rejects_out_of_range_status() -> None:
    defn = _sms_to_exit()
    defn["entry_node_id"] = "gt-write"
    defn["nodes"] = [
        {
            "type": "update_gotracker_appointment",
            "id": "gt-write",
            "next_node_id": "exit-1",
            "status_id": 10,
        },
        {"type": "exit", "id": "exit-1", "outcome": "updated"},
    ]
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
    defn["trigger"]["source"]["recall_interval_months"] = 0
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_recall_cooldown_defaults_to_decision_d() -> None:
    definition = WorkflowDefinition.model_validate(_with_wait())

    assert definition.trigger.type == "schedule"
    assert definition.trigger.source.reenrollment_cooldown_days == 90


def test_schedule_trigger_rejects_a_bad_cron_expression() -> None:
    defn = _with_wait()
    defn["trigger"]["cron"] = "not a cron"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_schedule_trigger_normalises_cron_whitespace() -> None:
    defn = _with_wait()
    defn["trigger"]["cron"] = "0   9  *  * *"

    definition = WorkflowDefinition.model_validate(defn)
    assert definition.trigger.cron == "0 9 * * *"


def test_fixed_timezone_mode_requires_a_real_timezone() -> None:
    defn = _with_wait()
    defn["trigger"]["timezone_mode"] = "fixed"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)

    defn["trigger"]["fixed_timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)

    defn["trigger"]["fixed_timezone"] = "America/Toronto"
    assert WorkflowDefinition.model_validate(defn).trigger.fixed_timezone == (
        "America/Toronto"
    )


def test_legacy_recall_scan_upconverts_to_a_schedule() -> None:
    defn = _with_wait()
    defn["trigger"] = {
        "type": "recall_scan",
        "recall_interval_months": 18,
        "recall_reenrollment_cooldown_days": 120,
    }

    d = WorkflowDefinition.model_validate(defn)

    assert d.trigger.type == "schedule"
    assert d.trigger.source.kind == "pms_recall"
    assert d.trigger.source.recall_interval_months == 18
    assert d.trigger.source.reenrollment_cooldown_days == 120


def test_recall_cooldown_must_be_positive() -> None:
    defn = _with_wait()
    defn["trigger"]["source"]["reenrollment_cooldown_days"] = 0
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_patient_status_changed_requires_statuses() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "patient_status_changed", "statuses": []}
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_patient_status_changed_rejects_blank_statuses() -> None:
    defn = _sms_to_exit()
    defn["trigger"] = {"type": "patient_status_changed", "statuses": ["  "]}
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(defn)


def test_retell_sms_conversation_has_profile_and_next_step_only() -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "sms_reply"},
            "entry_node_id": "chat-1",
            "nodes": [
                {
                    "type": "retell_sms_conversation",
                    "id": "chat-1",
                    "chat_profile_id": "profile-1",
                    "next_node_id": "exit-1",
                    # Legacy published definitions remain loadable, but author
                    # policy is discarded in favor of platform-owned policy.
                    "inactivity_timeout_seconds": 7200,
                    "dynamic_variable_mappings": [
                        {
                            "name": "appointment_reason",
                            "source_field": "appointment.reason",
                        }
                    ],
                },
                {"type": "exit", "id": "exit-1", "outcome": "done"},
            ],
        }
    )

    node = definition.nodes[0]
    assert node.type == "retell_sms_conversation"
    assert node.model_dump() == {
        "id": "chat-1",
        "type": "retell_sms_conversation",
        "chat_profile_id": "profile-1",
        "next_node_id": "exit-1",
    }


def test_retell_sms_conversation_ignores_all_legacy_author_policy() -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "chat-1",
            "nodes": [
                {
                    "type": "retell_sms_conversation",
                    "id": "chat-1",
                    "chat_profile_id": "profile-1",
                    "next_node_id": "exit-1",
                    "max_duration_seconds": 1,
                    "max_patient_turns": 999,
                    "human_handoff_tokens": ["HUMAN"],
                    "timeout_behavior": "handoff",
                    "failure_behavior": "continue",
                    "respect_quiet_hours": False,
                    "max_response_segments": 99,
                },
                {"type": "exit", "id": "exit-1"},
            ],
        }
    )

    assert set(definition.nodes[0].model_dump()) == {
        "id",
        "type",
        "chat_profile_id",
        "next_node_id",
    }
