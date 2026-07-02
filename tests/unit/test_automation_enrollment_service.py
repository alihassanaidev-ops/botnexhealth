"""Unit tests for AutomationWorkflowEnrollmentService."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowRun
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService


def _make_session(*, existing_run=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_run
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _make_run(status: str = AutomationRunStatus.PENDING.value) -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status=status,
    )


def test_enroll_creates_new_run_when_no_idempotency_key() -> None:
    session = _make_session()
    svc = AutomationWorkflowEnrollmentService(session)
    run, created = asyncio.run(
        svc.enroll(
            institution_id="inst-1",
            workflow_id="wf-1",
            workflow_version_id="ver-1",
        )
    )
    assert created is True
    assert run.status == AutomationRunStatus.PENDING.value
    assert session.add.call_count == 2  # run + enrolled event


def test_enroll_returns_existing_run_on_idempotency_match() -> None:
    existing = _make_run()
    session = _make_session(existing_run=existing)
    svc = AutomationWorkflowEnrollmentService(session)
    run, created = asyncio.run(
        svc.enroll(
            institution_id="inst-1",
            workflow_id="wf-1",
            workflow_version_id="ver-1",
            idempotency_key="appt-123",
        )
    )
    assert created is False
    assert run is existing
    session.add.assert_not_called()


def test_enroll_creates_new_run_when_no_match_for_idempotency_key() -> None:
    session = _make_session(existing_run=None)
    svc = AutomationWorkflowEnrollmentService(session)
    run, created = asyncio.run(
        svc.enroll(
            institution_id="inst-1",
            workflow_id="wf-1",
            workflow_version_id="ver-1",
            idempotency_key="appt-xyz",
        )
    )
    assert created is True
    assert run.idempotency_key == "appt-xyz"


def test_cancel_run_transitions_to_cancelled() -> None:
    session = _make_session()
    svc = AutomationWorkflowEnrollmentService(session)
    run = _make_run(AutomationRunStatus.RUNNING.value)
    result = asyncio.run(svc.cancel_run(run, reason="test-cancel"))
    assert result.status == AutomationRunStatus.CANCELLED.value
    assert result.blocked_reason == "test-cancel"
    assert result.cancelled_at is not None


def test_cancel_run_is_noop_for_completed() -> None:
    session = _make_session()
    svc = AutomationWorkflowEnrollmentService(session)
    run = _make_run(AutomationRunStatus.COMPLETED.value)
    result = asyncio.run(svc.cancel_run(run))
    assert result.status == AutomationRunStatus.COMPLETED.value
    session.flush.assert_not_awaited()


def test_cancel_run_is_noop_for_already_cancelled() -> None:
    session = _make_session()
    svc = AutomationWorkflowEnrollmentService(session)
    run = _make_run(AutomationRunStatus.CANCELLED.value)
    result = asyncio.run(svc.cancel_run(run))
    assert result.status == AutomationRunStatus.CANCELLED.value
    session.flush.assert_not_awaited()


def test_enroll_stores_trigger_fields() -> None:
    session = _make_session(existing_run=None)
    svc = AutomationWorkflowEnrollmentService(session)
    run, _ = asyncio.run(
        svc.enroll(
            institution_id="inst-1",
            workflow_id="wf-1",
            workflow_version_id="ver-1",
            trigger_type="appointment_offset",
            trigger_ref_type="appointment",
            trigger_ref_id="appt-99",
        )
    )
    assert run.trigger_type == "appointment_offset"
    assert run.trigger_ref_type == "appointment"
    assert run.trigger_ref_id == "appt-99"
