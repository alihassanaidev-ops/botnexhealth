"""Unit tests for AutomationWorkflowDefinitionService state machine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus
from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService


def _make_session(*, execute_returns=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = execute_returns
    mock_result.scalars.return_value.all.return_value = (
        execute_returns if isinstance(execute_returns, list) else []
    )
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _make_workflow(status: str) -> AutomationWorkflow:
    return AutomationWorkflow(
        institution_id="inst-1",
        name="Test Workflow",
        status=status,
    )


def test_create_draft_sets_draft_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    result = asyncio.run(svc.create_draft("inst-1", name="Reminder"))
    assert result.status == AutomationWorkflowStatus.DRAFT.value
    session.add.assert_called_once()
    session.flush.assert_awaited()


def test_create_draft_strips_and_rejects_empty_name() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    with pytest.raises(Exception):
        asyncio.run(svc.create_draft("inst-1", name="   "))


def test_update_draft_raises_when_not_draft() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ACTIVE.value)
    with pytest.raises(Exception):
        asyncio.run(svc.update_draft(workflow, name="New Name"))


def test_update_draft_mutates_allowed_fields() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.DRAFT.value)
    result = asyncio.run(svc.update_draft(workflow, name="Updated", description="desc"))
    assert result.name == "Updated"
    assert result.description == "desc"
    session.flush.assert_awaited()


def test_publish_version_transitions_draft_to_active() -> None:
    session = _make_session(execute_returns=None)
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.DRAFT.value)
    version = asyncio.run(svc.publish_version(workflow, {"nodes": []}, published_by_user_id="u-1"))
    assert workflow.status == AutomationWorkflowStatus.ACTIVE.value
    assert workflow.current_version_id == version.id
    assert version.version_number == 1
    assert version.definition_checksum is not None


def test_publish_version_increments_version_number() -> None:
    from src.app.models.automation_workflow import AutomationWorkflowVersion

    existing = AutomationWorkflowVersion(
        institution_id="inst-1",
        workflow_id="wf-1",
        version_number=3,
        definition={},
    )

    session = _make_session(execute_returns=existing)
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.PAUSED.value)
    version = asyncio.run(svc.publish_version(workflow, {"nodes": []}))
    assert version.version_number == 4


def test_publish_version_raises_for_archived() -> None:
    session = _make_session(execute_returns=None)
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ARCHIVED.value)
    with pytest.raises(Exception):
        asyncio.run(svc.publish_version(workflow, {}))


def test_pause_workflow_transitions_active_to_paused() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ACTIVE.value)
    asyncio.run(svc.pause_workflow(workflow))
    assert workflow.status == AutomationWorkflowStatus.PAUSED.value


def test_pause_workflow_raises_when_not_active() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.DRAFT.value)
    with pytest.raises(Exception):
        asyncio.run(svc.pause_workflow(workflow))


def test_resume_workflow_transitions_paused_to_active() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.PAUSED.value)
    asyncio.run(svc.resume_workflow(workflow))
    assert workflow.status == AutomationWorkflowStatus.ACTIVE.value


def test_resume_workflow_raises_when_not_paused() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ACTIVE.value)
    with pytest.raises(Exception):
        asyncio.run(svc.resume_workflow(workflow))


def test_archive_workflow_is_idempotent() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ARCHIVED.value)
    result = asyncio.run(svc.archive_workflow(workflow))
    assert result.status == AutomationWorkflowStatus.ARCHIVED.value
    session.flush.assert_not_awaited()


def test_archive_workflow_from_active() -> None:
    session = _make_session()
    svc = AutomationWorkflowDefinitionService(session)
    workflow = _make_workflow(AutomationWorkflowStatus.ACTIVE.value)
    asyncio.run(svc.archive_workflow(workflow))
    assert workflow.status == AutomationWorkflowStatus.ARCHIVED.value
