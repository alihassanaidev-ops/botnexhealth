"""Unit tests for campaign template library and template API routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.automation.campaign_templates import (
    TEMPLATES,
    get_template,
    instantiate_definition,
    list_templates,
    template_tokens,
)
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.api.routes.automation_templates import (
    CampaignTemplateInstantiateRequest,
    CampaignTemplateResponse,
    get_campaign_template,
    instantiate_template,
    list_campaign_templates,
)


# ---------------------------------------------------------------------------
# Template library — schema validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_id", list(TEMPLATES.keys()))
def test_template_definition_is_valid_workflow_schema(template_id: str) -> None:
    """Every template definition must pass WorkflowDefinition validation."""
    template = TEMPLATES[template_id]
    defn = WorkflowDefinition.model_validate(template.definition)
    assert defn.entry_node_id in {n.id for n in defn.nodes}


def test_priority_dental_templates_present() -> None:
    assert set(TEMPLATES.keys()) == {
        "surgery-pre-appointment-confirmation",
        "post-op-followup-after-confirmation",
    }


def test_list_templates_returns_all() -> None:
    assert len(list_templates()) == 2


def test_get_template_known_id() -> None:
    t = get_template("surgery-pre-appointment-confirmation")
    assert t is not None
    assert t.trigger_type == "appointment_offset"


def test_get_template_unknown_id_returns_none() -> None:
    assert get_template("nonexistent") is None


# ---------------------------------------------------------------------------
# Template trigger types
# ---------------------------------------------------------------------------


def test_appointment_templates_use_appointment_offset_trigger() -> None:
    t = TEMPLATES["surgery-pre-appointment-confirmation"]
    assert t.definition["trigger"]["type"] == "appointment_offset"


def test_surgery_confirmation_template_marks_confirmed_status() -> None:
    t = TEMPLATES["surgery-pre-appointment-confirmation"]
    nodes = {node["id"]: node for node in t.definition["nodes"]}

    assert nodes["write-gotracker-confirmed"]["type"] == "update_gotracker_appointment"
    assert nodes["write-gotracker-confirmed"]["confirmed"] is True
    assert nodes["write-gotracker-confirmed"]["preconfirmed"] is None
    assert nodes["write-gotracker-confirmed"]["next_node_id"] == "exit-confirmed"


def test_surgery_confirmation_template_configures_reasons_and_retry_timing() -> None:
    template = TEMPLATES["surgery-pre-appointment-confirmation"]

    definition = instantiate_definition(
        template,
        voice_profile_id="prof-surgery",
        setup_options={
            "appointment_reasons": ["Bridge Prep", "Implant Surgery"],
            "call_offset_hours_before": 36,
            "retry_delay_1_hours": 4,
            "retry_delay_2_hours": 7.5,
            "patient_voice_cooldown_hours": 12,
        },
    )

    assert "appointment_type_ids" not in definition["trigger"]
    assert definition["trigger"]["offset_hours"] == -36
    nodes = {node["id"]: node for node in definition["nodes"]}
    assert nodes["check-eligible-reason"]["rules"] == [
        {
            "field": "appointment_status_id",
            "op": "in_case_insensitive",
            "value": ["1"],
        },
        {
            "field": "appointment_reason",
            "op": "in_case_insensitive",
            "value": ["Bridge Prep", "Implant Surgery"],
        },
    ]
    assert nodes["wait-retry-1"]["delay"]["duration_seconds"] == 4 * 60 * 60
    assert nodes["wait-retry-2"]["delay"]["duration_seconds"] == int(7.5 * 60 * 60)
    for node_id in ("voice-preop-attempt-1", "voice-preop-attempt-2", "voice-preop-attempt-3"):
        assert nodes[node_id]["voice_profile_id"] == "prof-surgery"
        assert nodes[node_id]["retell_agent_id"] == ""
        assert nodes[node_id]["patient_voice_cooldown_hours"] == 12


def test_surgery_confirmation_template_allows_call_at_appointment_time() -> None:
    definition = instantiate_definition(
        TEMPLATES["surgery-pre-appointment-confirmation"],
        voice_profile_id="prof-surgery",
        setup_options={
            "appointment_reasons": ["Bridge Prep"],
            "call_offset_hours_before": 0,
        },
    )

    assert definition["trigger"]["offset_hours"] == 0


def test_surgery_confirmation_template_requires_at_least_one_reason() -> None:
    template = TEMPLATES["surgery-pre-appointment-confirmation"]

    with pytest.raises(ValueError, match="appointment_reasons"):
        instantiate_definition(
            template,
            voice_profile_id="prof-surgery",
            setup_options={"appointment_reasons": []},
        )


def test_surgery_confirmation_template_has_three_business_attempts_and_dynamic_callbacks() -> None:
    definition = instantiate_definition(
        TEMPLATES["surgery-pre-appointment-confirmation"],
        voice_profile_id="prof-surgery",
        setup_options={"appointment_reasons": ["bridge prep"]},
    )
    nodes = {node["id"]: node for node in definition["nodes"]}

    assert nodes["check-eligible-reason"]["true_next_node_id"] == "voice-preop-attempt-1"
    assert nodes["wait-retry-1"]["next_node_id"] == "voice-preop-attempt-2"
    assert nodes["wait-retry-2"]["next_node_id"] == "voice-preop-attempt-3"
    assert nodes["wait-callback-1"]["delay"] == {
        "delay_type": "appointment_relative",
        "offset_seconds": 0,
        "anchor_field": "callback_at",
    }
    assert nodes["wait-callback-1"]["next_node_id"] == "voice-preop-attempt-2"
    assert nodes["wait-callback-2"]["next_node_id"] == "voice-preop-attempt-3"
    assert nodes["mark-max-attempts"]["status"] == "unreachable_after_max_attempts"
    assert nodes["write-gotracker-rescheduled"]["start_time"] == "{{reschedule_start_time}}"


def test_surgery_confirmation_template_does_not_treat_answered_as_confirmed() -> None:
    template = TEMPLATES["surgery-pre-appointment-confirmation"]
    definition = instantiate_definition(
        template,
        voice_profile_id="prof-surgery",
        setup_options={"appointment_reasons": ["bridge prep"]},
    )
    nodes = {node["id"]: node for node in definition["nodes"]}

    confirmed_rule = nodes["attempt-1-confirmed"]["rules"][0]
    assert confirmed_rule == {
        "field": "call_outcome",
        "op": "eq",
        "value": "confirmed",
    }
    assert "answered" not in str(nodes["attempt-1-confirmed"]["rules"])
    assert nodes["attempt-1-cancelled"]["true_next_node_id"] == "write-gotracker-cancelled"
    assert nodes["write-gotracker-cancelled"]["type"] == "update_gotracker_appointment"
    assert nodes["write-gotracker-cancelled"]["status_id"] == 3
    assert nodes["attempt-1-reschedule"]["true_next_node_id"] == "check-reschedule-time"
    assert nodes["attempt-1-unreachable"]["true_next_node_id"] == "wait-retry-1"


def test_post_op_template_starts_from_completed_flow_state_and_waits_one_day() -> None:
    t = TEMPLATES["post-op-followup-after-confirmation"]
    nodes = {node["id"]: node for node in t.definition["nodes"]}

    assert t.definition["trigger"] == {
        "type": "appointment_state_changed",
        "status_ids": [],
        "confirmed": None,
        "preconfirmed": None,
        "flow_states": ["Completed"],
        "max_followup_delay_hours": 72,
        "campaign_goal": "post_op_followup",
    }
    assert nodes["check-post-op-eligible-reason"]["rules"] == [
        {
            "field": "appointment_reason",
            "op": "in_case_insensitive",
            "value": [],
        }
    ]
    assert nodes["wait-post-op"]["delay"] == {
        "delay_type": "appointment_relative",
        "offset_seconds": 86400,
        "anchor_field": "flow_changed_at",
    }
    assert nodes["voice-post-op"]["type"] == "send_voice"
    assert nodes["voice-post-op"]["wait_for_outcome"] is True
    assert nodes["voice-post-op"]["patient_voice_cooldown_behavior"] == "defer"
    assert nodes["voice-post-op"]["patient_voice_cooldown_deadline_field"] == "post_op_expires_at"
    assert nodes["check-post-op-needs-review"]["rules"] == [
        {"field": "call_outcome", "op": "neq", "value": "post_op_ok"}
    ]


def test_post_op_template_configures_reason_delay_deadline_and_cooldown() -> None:
    definition = instantiate_definition(
        TEMPLATES["post-op-followup-after-confirmation"],
        voice_profile_id="prof-post-op",
        setup_options={
            "post_op_reasons": ["Implant Surgery", "Extraction"],
            "post_op_delay_hours": 36,
            "post_op_latest_call_hours": 60,
            "patient_voice_cooldown_hours": 12,
        },
    )
    nodes = {node["id"]: node for node in definition["nodes"]}

    assert nodes["check-post-op-eligible-reason"]["rules"][0]["value"] == [
        "Implant Surgery",
        "Extraction",
    ]
    assert nodes["wait-post-op"]["delay"]["offset_seconds"] == 36 * 60 * 60
    assert definition["trigger"]["max_followup_delay_hours"] == 60
    assert nodes["voice-post-op"]["patient_voice_cooldown_hours"] == 12


def test_post_op_template_rejects_deadline_before_planned_call() -> None:
    with pytest.raises(ValueError, match="at least post_op_delay_hours"):
        instantiate_definition(
            TEMPLATES["post-op-followup-after-confirmation"],
            voice_profile_id="prof-post-op",
            setup_options={
                "post_op_reasons": ["Implant Surgery"],
                "post_op_delay_hours": 48,
                "post_op_latest_call_hours": 24,
            },
        )


def test_template_metadata_has_required_dental_contract() -> None:
    for template in TEMPLATES.values():
        metadata = template.metadata
        assert metadata.category in {
            "appointment_ops",
            "recall",
            "treatment",
            "callback",
            "reactivation",
        }
        assert metadata.goal
        assert metadata.outcome_labels
        assert metadata.supported_channels
        assert metadata.required_readiness_checks
        assert metadata.default_compliance_content_class in {
            "transactional_care",
            "recall",
            "sales",
            "marketing",
        }
        expected_daily_cap = 3 if template.id == "surgery-pre-appointment-confirmation" else 1
        assert metadata.default_frequency_cap.max_per_day == expected_daily_cap
        assert metadata.default_frequency_cap.max_per_rolling_7_days == 3
        assert metadata.analytics_outcome_map
        assert metadata.sample_preview_context


def test_template_tokens_are_cataloged_and_declared_when_required() -> None:
    catalog_names = {field.name for field in MERGE_FIELD_CATALOG}
    for template in TEMPLATES.values():
        tokens = set(template_tokens(template.definition))
        assert tokens <= catalog_names
        assert set(template.metadata.required_merge_fields) <= catalog_names
        assert set(template.metadata.required_merge_fields) <= (
            tokens | set(template.metadata.sample_preview_context.keys())
        )


# ---------------------------------------------------------------------------
# CampaignTemplateResponse.from_template
# ---------------------------------------------------------------------------


def test_campaign_template_response_from_template() -> None:
    t = get_template("surgery-pre-appointment-confirmation")
    resp = CampaignTemplateResponse.from_template(t)
    assert resp.id == "surgery-pre-appointment-confirmation"
    assert "voice" in resp.tags
    assert resp.category == "appointment_ops"
    assert resp.metadata["pms_capability_requirements"] == []


# ---------------------------------------------------------------------------
# Route: list_campaign_templates
# ---------------------------------------------------------------------------


def test_list_route_returns_all_templates() -> None:
    user = MagicMock()
    result = asyncio.run(list_campaign_templates(user))
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Route: get_campaign_template
# ---------------------------------------------------------------------------


def test_get_route_returns_template() -> None:
    user = MagicMock()
    result = asyncio.run(get_campaign_template("surgery-pre-appointment-confirmation", user))
    assert result.id == "surgery-pre-appointment-confirmation"


def test_get_route_unknown_id_raises_404() -> None:
    from fastapi import HTTPException
    user = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_campaign_template("bad-id", user))
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Route: instantiate_template
# ---------------------------------------------------------------------------


def _make_wf_mock():
    from datetime import datetime, timezone
    wf = MagicMock()
    wf.id = "wf-new"
    wf.name = "Surgery Pre-Appointment Confirmation"
    # Post-publish state is paused by the template instantiate route.
    wf.status = "draft"
    wf.trigger_type = "appointment_offset"
    wf.definition = TEMPLATES["surgery-pre-appointment-confirmation"].definition
    wf.current_version_id = "ver-1"
    wf.created_at = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)
    return wf


def test_instantiate_creates_publishes_and_pauses_workflow() -> None:
    """instantiate must create the draft, publish the template definition, and pause.

    Regression guard for the original bug: the route passed ``trigger_type`` and
    ``definition`` kwargs that ``create_draft`` does not accept (TypeError at
    runtime), and never persisted a version. It now mirrors
    ``POST /automation/workflows`` — create_draft then publish_version, followed
    by pause so launch events cannot enroll patients before review.
    """
    user = MagicMock()
    user.institution_id = "inst-1"
    user.id = "user-1"

    wf = _make_wf_mock()
    mock_svc = AsyncMock()
    mock_svc.create_draft = AsyncMock(return_value=wf)
    mock_svc.publish_version = AsyncMock()

    async def _pause_workflow(workflow):
        workflow.status = "paused"
        return workflow

    mock_svc.pause_workflow = AsyncMock(side_effect=_pause_workflow)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    import unittest.mock as mock
    with (
        mock.patch(
            "src.app.api.routes.automation_templates.get_db_session",
            return_value=mock_session,
        ),
        mock.patch(
            "src.app.api.routes.automation_templates.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        result = asyncio.run(
                instantiate_template(
                    "surgery-pre-appointment-confirmation",
                    user,
                    data=CampaignTemplateInstantiateRequest(
                        name="My Surgery Confirmation",
                        location_id="loc-1",
                        voice_profile_id="prof-surgery",
                        setup_options={"appointment_reasons": ["bridge prep"]},
                    ),
                )
            )

    assert result.id == "wf-new"
    assert result.status == "paused"
    assert result.trigger_type == "appointment_offset"
    # create_draft must NOT receive trigger_type/definition (the original bug).
    mock_svc.create_draft.assert_awaited_once()
    _, create_kwargs = mock_svc.create_draft.call_args
    assert "trigger_type" not in create_kwargs
    assert "definition" not in create_kwargs
    assert create_kwargs["name"] == "My Surgery Confirmation"
    assert create_kwargs["location_id"] == "loc-1"
    assert create_kwargs["category"] == "appointment_ops"
    # the template definition must be published as a version
    mock_svc.publish_version.assert_awaited_once()
    published_def = mock_svc.publish_version.call_args.args[1]
    assert "appointment_type_ids" not in published_def["trigger"]
    published_nodes = {node["id"]: node for node in published_def["nodes"]}
    assert published_nodes["voice-preop-attempt-1"]["voice_profile_id"] == "prof-surgery"
    assert published_nodes["voice-preop-attempt-1"]["retell_agent_id"] == ""
    reason_rule = next(
        rule
        for rule in published_nodes["check-eligible-reason"]["rules"]
        if rule["field"] == "appointment_reason"
    )
    assert reason_rule["value"] == ["bridge prep"]
    assert mock_svc.publish_version.call_args.kwargs["content_classification"] == "transactional_care"
    mock_svc.pause_workflow.assert_awaited_once_with(wf)


def test_instantiate_voice_template_without_agent_raises_422() -> None:
    from fastapi import HTTPException

    user = MagicMock()
    user.institution_id = "inst-1"
    user.id = "user-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("post-op-followup-after-confirmation", user))

    assert exc_info.value.status_code == 422


def test_hidden_templates_are_not_instantiable() -> None:
    from fastapi import HTTPException
    user = MagicMock()
    user.institution_id = "inst-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("unscheduled-treatment-followup", user))

    assert get_template("appointment-reminder-24h") is None
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Template not found"


def test_instantiate_unknown_template_raises_404() -> None:
    from fastapi import HTTPException
    user = MagicMock()
    user.institution_id = "inst-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("does-not-exist", user))

    assert exc_info.value.status_code == 404
