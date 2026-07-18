"""Unit tests for automation workflow API routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.routes.automation_workflows import (
    EnrollRequest,
    ValidateDefinitionRequest,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowRunResponse,
    _institution_id,
    cancel_run,
    create_workflow,
    enroll_in_workflow,
    get_run_status,
    get_workflow,
    list_merge_fields,
    list_workflow_versions,
    list_workflows,
    publish_workflow,
    router as workflows_router,
    validate_definition,
)

_NOW = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(institution_id="inst-1", location_id=None):
    u = MagicMock()
    u.institution_id = institution_id
    u.location_id = location_id
    return u


def _make_workflow(status="draft", version_id=None):
    wf = MagicMock()
    wf.id = "wf-1"
    wf.name = "Test Workflow"
    wf.status = status
    wf.trigger_type = "manual"
    wf.definition = {"trigger": {"type": "manual"}, "entry_node_id": "e1", "nodes": []}
    wf.current_version_id = version_id
    wf.created_at = _NOW
    wf.updated_at = _NOW
    return wf


def _make_run(status="waiting"):
    r = MagicMock()
    r.id = "run-1"
    r.workflow_id = "wf-1"
    r.institution_id = "inst-1"
    r.status = status
    r.current_step_id = None
    r.outcome = None
    r.started_at = _NOW
    r.completed_at = None
    r.created_at = _NOW
    r.trigger_metadata = {}
    return r


def _make_session(wf=None, run=None, version=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.flush = AsyncMock()

    async def _get(model, pk, **kwargs):
        from src.app.models.automation_workflow import AutomationWorkflowRun, AutomationWorkflowVersion
        if model is AutomationWorkflowRun:
            return run
        if model is AutomationWorkflowVersion:
            return version
        return None

    session.get = _get
    return session


# ---------------------------------------------------------------------------
# _institution_id helper
# ---------------------------------------------------------------------------


def test_institution_id_returns_string():
    user = _make_user(institution_id="inst-abc")
    assert _institution_id(user) == "inst-abc"


def test_institution_id_raises_on_none():
    user = _make_user(institution_id=None)
    with pytest.raises(HTTPException) as exc_info:
        _institution_id(user)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# WorkflowResponse.from_model
# ---------------------------------------------------------------------------


def test_workflow_response_from_model():
    wf = _make_workflow(status="draft")
    resp = WorkflowResponse.from_model(wf)
    assert resp.id == "wf-1"
    assert resp.status == "draft"
    assert resp.current_version_id is None


def test_workflow_response_from_model_with_version():
    wf = _make_workflow(status="active", version_id="ver-1")
    resp = WorkflowResponse.from_model(wf)
    assert resp.current_version_id == "ver-1"


# ---------------------------------------------------------------------------
# WorkflowRunResponse.from_model
# ---------------------------------------------------------------------------


def test_run_response_from_model():
    run = _make_run(status="waiting")
    resp = WorkflowRunResponse.from_model(run)
    assert resp.id == "run-1"
    assert resp.status == "waiting"
    assert resp.outcome is None


# ---------------------------------------------------------------------------
# create_workflow
# ---------------------------------------------------------------------------


def test_create_workflow_returns_201():
    user = _make_user()
    wf = _make_workflow()
    mock_svc = AsyncMock()
    mock_svc.create_draft = AsyncMock(return_value=wf)
    session = _make_session()

    data = WorkflowCreateRequest(
        name="Test",
        definition={"trigger": {"type": "manual"}, "entry_node_id": "e1", "nodes": []},
    )

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        result = asyncio.run(create_workflow(data, user))

    assert result.name == "Test Workflow"
    mock_svc.create_draft.assert_awaited_once()


# ---------------------------------------------------------------------------
# list_workflows
# ---------------------------------------------------------------------------


def test_list_workflows_returns_list():
    user = _make_user()
    wf = _make_workflow()
    mock_svc = AsyncMock()
    mock_svc.list_workflows = AsyncMock(return_value=[wf, wf])
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        result = asyncio.run(list_workflows(user))

    assert len(result) == 2


# ---------------------------------------------------------------------------
# get_workflow
# ---------------------------------------------------------------------------


def test_get_workflow_returns_workflow():
    user = _make_user()
    wf = _make_workflow()
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        result = asyncio.run(get_workflow("wf-1", user))

    assert result.id == "wf-1"


def test_get_workflow_not_found_raises_404():
    user = _make_user()
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=None)
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_workflow("wf-bad", user))

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# publish_workflow
# ---------------------------------------------------------------------------


def test_publish_workflow_calls_publish_version():
    user = _make_user()
    wf = _make_workflow(status="draft", version_id="ver-1")
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    mock_svc.publish_version = AsyncMock()
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        asyncio.run(publish_workflow("wf-1", user))

    mock_svc.publish_version.assert_awaited_once_with(wf)


# ---------------------------------------------------------------------------
# enroll_in_workflow
# ---------------------------------------------------------------------------


def test_enroll_rejects_non_active_workflow():
    user = _make_user()
    wf = _make_workflow(status="draft")
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    session = _make_session()

    data = EnrollRequest(idempotency_key="key-1")

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(enroll_in_workflow("wf-1", data, user))

    assert exc_info.value.status_code == 409
    assert "not active" in exc_info.value.detail


def test_enroll_rejects_workflow_without_version():
    user = _make_user()
    wf = _make_workflow(status="active", version_id=None)
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    session = _make_session()

    data = EnrollRequest(idempotency_key="key-1")

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(enroll_in_workflow("wf-1", data, user))

    assert exc_info.value.status_code == 409
    assert "no published version" in exc_info.value.detail


def test_enroll_idempotent_returns_existing_run():
    """Duplicate idempotency_key returns existing run without re-advancing."""
    user = _make_user()
    wf = _make_workflow(status="active", version_id="ver-1")
    existing_run = _make_run(status="completed")

    def_svc = AsyncMock()
    def_svc.get_workflow = AsyncMock(return_value=wf)

    enroll_svc = AsyncMock()
    enroll_svc.enroll = AsyncMock(return_value=(existing_run, False))  # created=False

    session = _make_session()

    data = EnrollRequest(idempotency_key="dup-key")

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=def_svc,
        ),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowEnrollmentService",
            return_value=enroll_svc,
        ),
    ):
        result = asyncio.run(enroll_in_workflow("wf-1", data, user))

    assert result.status == "completed"


# ---------------------------------------------------------------------------
# get_run_status
# ---------------------------------------------------------------------------


def test_get_run_status_returns_run():
    user = _make_user()
    run = _make_run(status="waiting")
    session = _make_session(run=run)

    with patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session):
        result = asyncio.run(get_run_status("wf-1", "run-1", user))

    assert result.status == "waiting"


def test_get_run_status_wrong_workflow_raises_404():
    user = _make_user()
    run = _make_run(status="waiting")
    session = _make_session(run=run)

    with patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_run_status("wf-OTHER", "run-1", user))

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# cancel_run
# ---------------------------------------------------------------------------


def test_cancel_run_calls_cancel():
    user = _make_user()
    run = _make_run(status="waiting")
    session = _make_session(run=run)
    enroll_svc = AsyncMock()
    enroll_svc.cancel_run = AsyncMock()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowEnrollmentService",
            return_value=enroll_svc,
        ),
    ):
        asyncio.run(cancel_run("wf-1", "run-1", user))

    enroll_svc.cancel_run.assert_awaited_once_with(run)


# ---------------------------------------------------------------------------
# validate_definition  (finding #2 — backend node-linked validation endpoint)
# ---------------------------------------------------------------------------


_VALID_DEF = {
    "trigger": {"type": "manual"},
    "entry_node_id": "e1",
    "nodes": [{"type": "exit", "id": "e1", "outcome": "done"}],
}


def test_validate_accepts_valid_definition():
    user = _make_user()
    result = asyncio.run(validate_definition(ValidateDefinitionRequest(definition=_VALID_DEF), user))
    assert result.valid is True
    # A structurally-valid sending workflow with no content class is publishable
    # but surfaces a (non-blocking) content-class warning, never an error.
    assert not any(issue.severity == "error" for issue in result.issues)


def test_validate_reports_missing_exit_node():
    user = _make_user()
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "s1"},
        ],
    }
    result = asyncio.run(validate_definition(ValidateDefinitionRequest(definition=definition), user))
    assert result.valid is False
    assert any("exit node" in issue.message for issue in result.issues)


def test_validate_links_field_error_to_node_id():
    """A node-level field error must carry the offending node's declared id."""
    user = _make_user()
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "", "next_node_id": "x1"},
            {"type": "exit", "id": "x1"},
        ],
    }
    result = asyncio.run(validate_definition(ValidateDefinitionRequest(definition=definition), user))
    assert result.valid is False
    assert any(issue.node_id == "s1" for issue in result.issues)


# ---------------------------------------------------------------------------
# list_workflow_versions  (finding #6 — version history endpoint)
# ---------------------------------------------------------------------------


def _make_version(version_id, number):
    v = MagicMock()
    v.id = version_id
    v.workflow_id = "wf-1"
    v.version_number = number
    v.definition = {"trigger": {"type": "manual"}, "entry_node_id": "e1", "nodes": []}
    v.definition_checksum = f"sum-{number}"
    v.content_classification = None
    v.published_by_user_id = "user-1"
    v.published_at = _NOW
    v.created_at = _NOW
    return v


def test_list_versions_returns_newest_first_with_current_flag():
    user = _make_user()
    v1 = _make_version("ver-1", 1)
    v2 = _make_version("ver-2", 2)
    wf = _make_workflow(status="active", version_id="ver-2")
    # relationship returns versions unordered; the route must sort them.
    wf.versions = [v1, v2]

    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        result = asyncio.run(list_workflow_versions("wf-1", user))

    assert [v.version_number for v in result] == [2, 1]
    assert result[0].is_current is True
    assert result[1].is_current is False


def test_list_versions_workflow_not_found_raises_404():
    user = _make_user()
    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=None)
    session = _make_session()

    with (
        patch("src.app.api.routes.automation_workflows.get_db_session", return_value=session),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(list_workflow_versions("wf-bad", user))

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# list_merge_fields  (finding #3 — merge-field catalog, unblocked by Plans 04/05)
# ---------------------------------------------------------------------------


def test_list_merge_fields_returns_catalog_with_tokens():
    user = _make_user()
    result = asyncio.run(list_merge_fields(user))
    names = {f.name for f in result}
    assert {
        "patient_first_name",
        "patient_last_name",
        "patient_full_name",
        "clinic_name",
    } <= names
    for f in result:
        assert f.token == "{{" + f.name + "}}"
        assert f.label and f.sample and f.group
        assert f.availability in {"required_context", "optional_context", "derived"}
        assert f.phi_level in {"none", "low", "medium", "high"}
        assert f.channels
        assert f.trigger_types


def test_list_merge_fields_filters_by_trigger_and_channel():
    user = _make_user()
    result = asyncio.run(
        list_merge_fields(user, trigger_type="appointment_offset", channel="sms")
    )
    names = {f.name for f in result}

    assert "appointment_date" in names
    assert "provider_name" in names
    assert "recall_due_date" not in names
    assert "appointment_type" not in names  # high-PHI appointment type is email-only


def test_merge_field_catalog_does_not_drift_from_renderer():
    """Every catalog field must actually be substituted by the renderer.

    This is the drift guard: the catalog is sourced from the renderer's
    STATIC_MERGE_FIELDS, so a template of all catalog tokens must render with no
    raw {{...}} left behind.
    """
    from types import SimpleNamespace

    from src.app.services.automation.template_renderer import (
        STATIC_MERGE_FIELDS,
        render_sms_body,
    )

    contact = SimpleNamespace(first_name="Jordan", last_name="Rivera", full_name="Jordan Rivera")
    location = SimpleNamespace(name="Riverside Dental")
    template = " ".join("{{" + f.name + "}}" for f in STATIC_MERGE_FIELDS)

    rendered = render_sms_body(template, contact, location, {})

    assert "{{" not in rendered and "}}" not in rendered
    assert "Jordan" in rendered and "Riverside Dental" in rendered


def test_merge_fields_route_declared_before_workflow_id():
    """Guard the route-shadowing trap: the literal /merge-fields path must be
    matched before the parameterised /{workflow_id} route."""
    paths = [getattr(r, "path", "") for r in workflows_router.routes]
    mf = paths.index("/automation/workflows/merge-fields")
    wid = paths.index("/automation/workflows/{workflow_id}")
    assert mf < wid
