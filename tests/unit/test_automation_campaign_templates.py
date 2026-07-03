"""Unit tests for campaign template library and template API routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.automation.campaign_templates import (
    TEMPLATES,
    get_template,
    list_templates,
)
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.api.routes.automation_templates import (
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


def test_all_four_templates_present() -> None:
    assert set(TEMPLATES.keys()) == {
        "appointment-reminder-24h",
        "appointment-confirmation-48h",
        "recall-sms-6month",
        "reactivation-sms-email-18month",
    }


def test_list_templates_returns_all() -> None:
    assert len(list_templates()) == 4


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


def test_recall_templates_use_recall_scan_trigger() -> None:
    for tid in ("recall-sms-6month", "reactivation-sms-email-18month"):
        t = TEMPLATES[tid]
        assert t.definition["trigger"]["type"] == "recall_scan"


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


# ---------------------------------------------------------------------------
# Route: list_campaign_templates
# ---------------------------------------------------------------------------


def test_list_route_returns_all_templates() -> None:
    user = MagicMock()
    result = asyncio.run(list_campaign_templates(user))
    assert len(result) == 4


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
        result = asyncio.run(instantiate_template("appointment-reminder-24h", user))

    assert result.id == "wf-new"
    assert result.status == "active"
    assert result.trigger_type == "appointment_offset"
    # create_draft must NOT receive trigger_type/definition (the original bug).
    mock_svc.create_draft.assert_awaited_once()
    _, create_kwargs = mock_svc.create_draft.call_args
    assert "trigger_type" not in create_kwargs
    assert "definition" not in create_kwargs
    # the template definition must be published as a version
    mock_svc.publish_version.assert_awaited_once()
    published_def = mock_svc.publish_version.call_args.args[1]
    assert published_def == TEMPLATES["appointment-reminder-24h"].definition


def test_instantiate_unknown_template_raises_404() -> None:
    from fastapi import HTTPException
    user = MagicMock()
    user.institution_id = "inst-1"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(instantiate_template("does-not-exist", user))

    assert exc_info.value.status_code == 404
