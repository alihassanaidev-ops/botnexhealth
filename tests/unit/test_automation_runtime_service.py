"""Unit tests for AutomationWorkflowRuntimeService state transitions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
)
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService


def _make_session(max_existing_attempt=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    # begin_step queries MAX(attempt_number) for (run, step) to allocate the next
    # attempt. Default None → first attempt is 1.
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=max_existing_attempt)
    session.execute = AsyncMock(return_value=exec_result)
    return session


def _make_run(status: str = AutomationRunStatus.PENDING.value) -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status=status,
    )


def _make_step(
    step_id: str = "step-send-sms",
    status: str = AutomationStepStatus.PENDING.value,
) -> AutomationWorkflowStepExecution:
    return AutomationWorkflowStepExecution(
        institution_id="inst-1",
        workflow_run_id="run-1",
        workflow_version_id="ver-1",
        step_id=step_id,
        step_type="send_sms",
        status=status,
    )


# --- start_run ---

def test_start_run_pending_to_running() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.PENDING.value)
    result = asyncio.run(svc.start_run(run))
    assert result.status == AutomationRunStatus.RUNNING.value
    assert result.started_at is not None


def test_start_run_raises_if_not_pending() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    with pytest.raises(ValueError, match="pending"):
        asyncio.run(svc.start_run(run))


# --- begin_step ---

def test_begin_step_creates_execution_and_updates_run_pointer() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    step = asyncio.run(svc.begin_step(run, step_id="step-wait", step_type="wait"))
    assert step.step_id == "step-wait"
    assert step.status == AutomationStepStatus.PENDING.value
    assert step.attempt_number == 1  # first attempt for this (run, step)
    assert run.current_step_id == "step-wait"
    session.add.assert_called()


def test_begin_step_auto_increments_attempt_number() -> None:
    """A node begun again (e.g. after a quiet-hours hold resumes) gets the next
    attempt number, avoiding the (run, step, attempt) unique-index collision."""
    session = _make_session(max_existing_attempt=1)
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    step = asyncio.run(svc.begin_step(run, step_id="step-send-sms", step_type="send_sms"))
    assert step.attempt_number == 2


def test_begin_step_honors_explicit_attempt_number() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    step = asyncio.run(
        svc.begin_step(run, step_id="s", step_type="send_sms", attempt_number=3)
    )
    assert step.attempt_number == 3


# --- complete_step ---

def test_complete_step_sets_completed_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    step = _make_step()
    result = asyncio.run(svc.complete_step(step, result_code="sent"))
    assert result.status == AutomationStepStatus.COMPLETED.value
    assert result.result_code == "sent"
    assert result.completed_at is not None


# --- fail_step ---

def test_fail_step_sets_failed_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    step = _make_step()
    result = asyncio.run(svc.fail_step(step, error_message="timeout"))
    assert result.status == AutomationStepStatus.FAILED.value
    assert result.error_message == "timeout"


# --- wait_run ---

def test_wait_run_transitions_both_run_and_step() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    step = _make_step(status=AutomationStepStatus.PENDING.value)
    asyncio.run(svc.wait_run(run, step))
    assert run.status == AutomationRunStatus.WAITING.value
    assert step.status == AutomationStepStatus.WAITING.value


# --- resume_run ---

def test_resume_run_transitions_waiting_to_running() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.WAITING.value)
    step = _make_step(status=AutomationStepStatus.WAITING.value)
    asyncio.run(svc.resume_run(run, step))
    assert run.status == AutomationRunStatus.RUNNING.value
    assert step.status == AutomationStepStatus.COMPLETED.value
    assert step.completed_at is not None


def test_resume_run_raises_if_not_waiting() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    step = _make_step()
    with pytest.raises(ValueError, match="waiting"):
        asyncio.run(svc.resume_run(run, step))


# --- complete_run ---

def test_complete_run_transitions_to_completed() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    result = asyncio.run(svc.complete_run(run, outcome="confirmed"))
    assert result.status == AutomationRunStatus.COMPLETED.value
    assert result.outcome == "confirmed"
    assert result.completed_at is not None


def test_complete_run_is_noop_for_terminal_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.CANCELLED.value)
    result = asyncio.run(svc.complete_run(run))
    assert result.status == AutomationRunStatus.CANCELLED.value
    session.flush.assert_not_awaited()


# --- fail_run ---

def test_fail_run_transitions_to_failed() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    result = asyncio.run(svc.fail_run(run, reason="step retry exhausted"))
    assert result.status == AutomationRunStatus.FAILED.value
    assert result.blocked_reason == "step retry exhausted"


def test_fail_run_is_noop_for_already_failed() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.FAILED.value)
    result = asyncio.run(svc.fail_run(run))
    assert result.status == AutomationRunStatus.FAILED.value
    session.flush.assert_not_awaited()


# --- event emission ---

def test_events_are_emitted_on_state_changes() -> None:
    session = _make_session()
    svc = AutomationWorkflowRuntimeService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    asyncio.run(svc.complete_run(run, outcome="done"))
    # session.add called for run.completed event
    added_types = [type(call.args[0]).__name__ for call in session.add.call_args_list]
    assert "AutomationWorkflowEvent" in added_types
