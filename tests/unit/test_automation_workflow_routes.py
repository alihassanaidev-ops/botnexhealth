"""Unit tests for automation workflow API routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.routes.automation_workflows import (
    EnrollRequest,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowRunResponse,
    WorkflowUpdateRequest,
    _get_workflow_or_404,
    _institution_id,
    archive_workflow,
    cancel_run,
    create_workflow,
    enroll_in_workflow,
    get_run_status,
    get_workflow,
    list_workflows,
    pause_workflow,
    publish_workflow,
    resume_workflow,
    update_workflow,
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
