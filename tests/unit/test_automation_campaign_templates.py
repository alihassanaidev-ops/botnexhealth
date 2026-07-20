"""Unit tests for campaign template library and template API routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.automation.campaign_templates import (
    TEMPLATES,
    VOICE_AGENT_PLACEHOLDER,
    get_template,
    instantiate_definition,
    list_templates,
    template_tokens,
)
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.pms_capability_service import (
    CapabilityDetail,
    PmsCapabilityEvaluation,
)
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
        "appointment-reminder-24h",
        "appointment-confirmation-48h",
        "recall-sms-6month",
        "reactivation-sms-email-18month",
        "no-show-recovery",
        "cancellation-rebooking",
        "callback-automation",
        "unscheduled-treatment-followup",
    }


def test_list_templates_returns_all() -> None:
    assert len(list_templates()) == 8


def test_get_template_known_id() -> None:
    t = get_template("appointment-reminder-24h")
    assert t is not None
    assert t.trigger_type == "appointment_offset"


def test_get_template_unknown_id_returns_none() -> None:
    assert get_template("nonexistent") is None


# ---------------------------------------------------------------------------
# Template trigger types
# ---------------------------------------------------------------------------


def test_appointment_templates_use_appointment_offset_trigger() -> None:
    for tid in ("appointment-reminder-24h", "appointment-confirmation-48h"):
        t = TEMPLATES[tid]
        assert t.definition["trigger"]["type"] == "appointment_offset"


def test_confirmation_template_does_not_advertise_cancel_keyword() -> None:
    t = TEMPLATES["appointment-confirmation-48h"]
    sms = next(node for node in t.definition["nodes"] if node["id"] == "sms-confirm")
    body = sms["body_template"]
    assert "Reply YES to confirm." in body
    assert "CANCEL" not in body
    assert "Reply STOP to opt out." in body


def test_recall_templates_use_recall_scan_trigger() -> None:
    for tid in ("recall-sms-6month", "reactivation-sms-email-18month"):
        t = TEMPLATES[tid]
        assert t.definition["trigger"]["type"] == "recall_scan"


def test_callback_template_requires_voice_agent_substitution() -> None:
    template = TEMPLATES["callback-automation"]
    assert any(
        node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER
        for node in template.definition["nodes"]
        if node["type"] == "send_voice"
    )

    with pytest.raises(ValueError):
        instantiate_definition(template)

    definition = instantiate_definition(template, voice_agent_id="agent_clinic_1")
    voice = next(node for node in definition["nodes"] if node["type"] == "send_voice")
    assert voice["retell_agent_id"] == "agent_clinic_1"
    assert voice["wait_for_outcome"] is True

    condition = next(node for node in definition["nodes"] if node["id"] == "check-call-outcome")
    assert condition["rules"][0] == {
        "field": "call_outcome",
        "op": "in",
        "value": ["answered", "transferred"],
    }
    assert any(
        node["type"] == "exit" and node.get("outcome") == "staff_handoff"
        for node in definition["nodes"]
    )


def test_callback_template_metadata_exposes_voice_outcome_readiness() -> None:
    metadata = TEMPLATES["callback-automation"].metadata

    assert "voice_outcome_wait" in metadata.required_readiness_checks
    assert "callback_queue_source" in metadata.required_readiness_checks
    assert {"answered", "booked", "transferred", "staff_handoff", "unreachable", "do_not_call"} <= set(metadata.outcome_labels)
    assert metadata.analytics_outcome_map["failed"] == "unreachable"


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
        assert metadata.default_frequency_cap.max_per_day == 1
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
# Reactivation template has multi-step flow with exit nodes
# ---------------------------------------------------------------------------


def test_reactivation_template_has_multiple_exit_nodes() -> None:
    t = TEMPLATES["reactivation-sms-email-18month"]
    defn = WorkflowDefinition.model_validate(t.definition)
    exit_nodes = [n for n in defn.nodes if n.type == "exit"]
    assert len(exit_nodes) >= 2


# ---------------------------------------------------------------------------
# CampaignTemplateResponse.from_template
# ---------------------------------------------------------------------------


def test_campaign_template_response_from_template() -> None:
    t = get_template("recall-sms-6month")
    resp = CampaignTemplateResponse.from_template(t)
    assert resp.id == "recall-sms-6month"
    assert "sms" in resp.tags
    assert resp.category == "recall"
    assert resp.metadata["pms_capability_requirements"] == ["patient_recalls"]


# ---------------------------------------------------------------------------
# Route: list_campaign_templates
# ---------------------------------------------------------------------------


def test_list_route_returns_all_templates() -> None:
    user = MagicMock()
    result = asyncio.run(list_campaign_templates(user))
    assert len(result) == 8


# ---------------------------------------------------------------------------
# Route: get_campaign_template
# ---------------------------------------------------------------------------


def test_get_route_returns_template() -> None:
    user = MagicMock()
    result = asyncio.run(get_campaign_template("appointment-reminder-24h", user))
    assert result.id == "appointment-reminder-24h"


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
    wf.name = "Appointment Reminder (24h)"
    # Post-publish state: create_draft + publish_version leaves the workflow active.
    wf.status = "active"
    wf.trigger_type = "appointment_offset"
    wf.definition = TEMPLATES["appointment-reminder-24h"].definition
    wf.current_version_id = "ver-1"
    wf.created_at = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)
    return wf


def test_instantiate_creates_and_publishes_workflow() -> None:
    """instantiate must create the draft AND publish the template definition.

    Regression guard for the original bug: the route passed ``trigger_type`` and
    ``definition`` kwargs that ``create_draft`` does not accept (TypeError at
    runtime), and never persisted a version. It now mirrors
    ``POST /automation/workflows`` — create_draft then publish_version.
    """
    user = MagicMock()
    user.institution_id = "inst-1"
    user.id = "user-1"

    wf = _make_wf_mock()
    mock_svc = AsyncMock()
    mock_svc.create_draft = AsyncMock(return_value=wf)
    mock_svc.publish_version = AsyncMock()

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
                "appointment-reminder-24h",
                user,
                data=CampaignTemplateInstantiateRequest(
                    name="My Reminder",
                    location_id="loc-1",
                ),
            )
        )

    assert result.id == "wf-new"
    assert result.status == "active"
    assert result.trigger_type == "appointment_offset"
    # create_draft must NOT receive trigger_type/definition (the original bug).
    mock_svc.create_draft.assert_awaited_once()
    _, create_kwargs = mock_svc.create_draft.call_args
    assert "trigger_type" not in create_kwargs
    assert "definition" not in create_kwargs
    assert create_kwargs["name"] == "My Reminder"
    assert create_kwargs["location_id"] == "loc-1"
    assert create_kwargs["category"] == "appointment_ops"
    # the template definition must be published as a version
    mock_svc.publish_version.assert_awaited_once()
    published_def = mock_svc.publish_version.call_args.args[1]
    assert published_def == TEMPLATES["appointment-reminder-24h"].definition
    assert mock_svc.publish_version.call_args.kwargs["content_classification"] == "transactional_care"


def test_instantiate_voice_template_without_agent_raises_422() -> None:
    from fastapi import HTTPException

    user = MagicMock()
    user.institution_id = "inst-1"
    user.id = "user-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("callback-automation", user))

    assert exc_info.value.status_code == 422


def test_instantiate_blocks_unsupported_pms_capability() -> None:
    from fastapi import HTTPException
    import unittest.mock as mock

    user = MagicMock()
    user.institution_id = "inst-1"
    user.id = "user-1"

    institution = MagicMock()
    location = MagicMock()
    evaluation = PmsCapabilityEvaluation(
        requirements=["treatment_plans"],
        supported=False,
        status="unsupported",
        pms_name="Dentrix Ascend",
        missing=["treatment_plans"],
        partial=[],
        unknown=[],
        details={
            "treatment_plans": CapabilityDetail(
                capability="treatment_plans",
                status="unsupported",
                label="treatment plans",
                matched_api="View treatment plans",
                raw_value="no",
            )
        },
        message="Dentrix Ascend does not support: treatment_plans.",
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        mock.patch(
            "src.app.api.routes.automation_templates.get_db_session",
            return_value=mock_session,
        ),
        mock.patch(
            "src.app.api.routes.automation_templates._resolve_institution_location",
            new=AsyncMock(return_value=(institution, location)),
        ),
        mock.patch(
            "src.app.services.automation.pms_capability_service.PmsCapabilityService.evaluate_location",
            new=AsyncMock(return_value=evaluation),
        ),
        mock.patch(
            "src.app.api.routes.automation_templates.AutomationWorkflowDefinitionService"
        ) as service_cls,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                instantiate_template(
                    "unscheduled-treatment-followup",
                    user,
                    data=CampaignTemplateInstantiateRequest(location_id="loc-1"),
                )
            )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "unsupported_pms_capability"
    service_cls.assert_not_called()


def test_instantiate_unknown_template_raises_404() -> None:
    from fastapi import HTTPException
    user = MagicMock()
    user.institution_id = "inst-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("does-not-exist", user))

    assert exc_info.value.status_code == 404
