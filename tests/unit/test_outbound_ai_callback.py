"""Unit tests for Plan 07 — AI Callback (callback_requested trigger + enrollment)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.automation_workflow import AutomationWorkflowStatus
from src.app.services.automation.callback_trigger_service import (
    CallbackTriggerService,
    compute_callback_eta,
    make_callback_idempotency_key,
)
from src.app.services.automation.definition_schema import (
    CallbackRequestedTrigger,
    WorkflowDefinition,
)
from src.app.tasks.automation_workflow import _trigger_callback_async

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(trigger_type="callback_requested", version_id="ver-1"):
    wf = MagicMock()
    wf.id = "wf-1"
    wf.institution_id = "inst-1"
    wf.status = AutomationWorkflowStatus.ACTIVE.value
    wf.trigger_type = trigger_type
    wf.current_version_id = version_id
    wf.definition = {
        "trigger": {"type": trigger_type},
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }
    return wf


def _make_session(workflows=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = workflows or []
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# Trigger schema
# ---------------------------------------------------------------------------


def test_callback_trigger_parses_in_definition():
    defn = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "callback_requested"},
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        }
    )
    assert isinstance(defn.trigger, CallbackRequestedTrigger)
    assert defn.trigger.type == "callback_requested"


# ---------------------------------------------------------------------------
# compute_callback_eta
# ---------------------------------------------------------------------------


def test_eta_none_when_no_preferred_time():
    assert compute_callback_eta(None, _NOW) is None


def test_eta_none_when_preferred_time_in_past():
    assert compute_callback_eta(_NOW - timedelta(hours=1), _NOW) is None


def test_eta_none_when_preferred_time_naive():
    naive = (_NOW + timedelta(hours=2)).replace(tzinfo=None)
    assert compute_callback_eta(naive, _NOW) is None


def test_eta_returns_future_preferred_time():
    future = _NOW + timedelta(hours=3)
    assert compute_callback_eta(future, _NOW) == future


# ---------------------------------------------------------------------------
# make_callback_idempotency_key
# ---------------------------------------------------------------------------


def test_idempotency_key_format():
    assert make_callback_idempotency_key("ver-abc", "call-123") == "callback:ver-abc:call-123"


def test_idempotency_key_stable():
    assert make_callback_idempotency_key("v", "c") == make_callback_idempotency_key("v", "c")


# ---------------------------------------------------------------------------
# CallbackTriggerService.find_active_callback_workflows
# ---------------------------------------------------------------------------


def test_find_active_callback_workflows_filters_by_trigger_type():
    callback_wf = _make_workflow(trigger_type="callback_requested")
    other_wf = _make_workflow(trigger_type="appointment_offset")
    session = _make_session(workflows=[callback_wf, other_wf])

    async def run():
        return await CallbackTriggerService(session).find_active_callback_workflows("inst-1")

    results = asyncio.run(run())
    assert [wf.trigger_type for wf in results] == ["callback_requested"]
    session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# _trigger_callback_async — schedules enrollment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_callback_no_workflows_schedules_nothing():
    mock_session = _make_session(workflows=[])
    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.CallbackTriggerService"
    ) as MockSvc, patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as mock_task:
        MockSvc.return_value.find_active_callback_workflows = AsyncMock(return_value=[])
        mock_task.apply_async = MagicMock()

        result = await _trigger_callback_async(
            institution_id="inst-1",
            call_id="call-1",
            contact_id="c-1",
            location_id="l-1",
            preferred_callback_at_iso=None,
            trigger_metadata={},
        )

    assert result["scheduled"] == 0
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_callback_schedules_with_immediate_eta():
    wf = _make_workflow()
    mock_session = _make_session(workflows=[wf])
    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.CallbackTriggerService"
    ) as MockSvc, patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as mock_task:
        MockSvc.return_value.find_active_callback_workflows = AsyncMock(return_value=[wf])
        mock_task.apply_async = MagicMock()

        result = await _trigger_callback_async(
            institution_id="inst-1",
            call_id="call-1",
            contact_id="c-1",
            location_id="l-1",
            preferred_callback_at_iso=None,  # → immediate
            trigger_metadata={"source": "webhook"},
        )

    assert result["scheduled"] == 1
    mock_task.apply_async.assert_called_once()
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["eta"] is None  # immediate
    assert kwargs["queue"] == "workflow"
    task_kwargs = kwargs["kwargs"]
    assert task_kwargs["trigger_type"] == "callback_requested"
    assert task_kwargs["trigger_ref_type"] == "call"
    assert task_kwargs["trigger_ref_id"] == "call-1"
    assert task_kwargs["idempotency_key"] == "callback:ver-1:call-1"
    assert task_kwargs["contact_id"] == "c-1"


@pytest.mark.asyncio
async def test_trigger_callback_honors_future_preferred_time():
    wf = _make_workflow()
    mock_session = _make_session(workflows=[wf])
    future = datetime.now(tz=timezone.utc) + timedelta(hours=4)
    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.CallbackTriggerService"
    ) as MockSvc, patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as mock_task:
        MockSvc.return_value.find_active_callback_workflows = AsyncMock(return_value=[wf])
        mock_task.apply_async = MagicMock()

        await _trigger_callback_async(
            institution_id="inst-1",
            call_id="call-1",
            contact_id="c-1",
            location_id="l-1",
            preferred_callback_at_iso=future.isoformat(),
            trigger_metadata={},
        )

    assert mock_task.apply_async.call_args.kwargs["eta"] == future
