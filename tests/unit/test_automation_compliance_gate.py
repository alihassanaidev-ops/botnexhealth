"""Unit tests for compliance gate protocol and dispatcher integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.compliance_gate import (
    ComplianceGate,
    GateResult,
    NoOpComplianceGate,
)
from src.app.services.automation.definition_schema import (
    AppointmentOffsetTrigger,
    ExitNode,
    SendSmsNode,
    WorkflowDefinition,
)
from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher
from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowRun


def _make_run():
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status=AutomationRunStatus.RUNNING.value,
    )


def _make_runtime():
    rt = AsyncMock()
    step = MagicMock()
    step.id = "step-1"
    rt.begin_step = AsyncMock(return_value=step)
    rt.complete_step = AsyncMock(return_value=step)
    rt.fail_step = AsyncMock(return_value=step)
    rt.complete_run = AsyncMock()
    rt.fail_run = AsyncMock()
    rt.wait_run = AsyncMock()
    return rt


def _make_session():
    s = AsyncMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    s.execute = AsyncMock(return_value=mock_result)
    return s


def _sms_to_exit_definition():
    return WorkflowDefinition(
        trigger=AppointmentOffsetTrigger(offset_hours=-24),
        entry_node_id="sms-1",
        nodes=[
            SendSmsNode(id="sms-1", body_template="Hi", next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="sent"),
        ],
    )


class _BlockGate:
    async def check(self, run, channel_type):
        return GateResult(action="block", reason="opt_out")


class _HoldGate:
    async def check(self, run, channel_type):
        return GateResult(action="hold", reason="consent_pending")


class _AllowGate:
    async def check(self, run, channel_type):
        return GateResult(action="allow")


# ---------------------------------------------------------------------------
# NoOpComplianceGate
# ---------------------------------------------------------------------------


def test_noop_gate_always_allows():
    gate = NoOpComplianceGate()
    run = _make_run()
    result = asyncio.run(gate.check(run, "send_sms"))
    assert result.action == "allow"
    assert result.reason is None


def test_noop_gate_satisfies_protocol():
    assert isinstance(NoOpComplianceGate(), ComplianceGate)


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------


def test_gate_result_defaults():
    r = GateResult(action="allow")
    assert r.reason is None


def test_gate_result_with_reason():
    r = GateResult(action="block", reason="opt_out")
    assert r.reason == "opt_out"


# ---------------------------------------------------------------------------
# Dispatcher: gate=allow → send proceeds
# ---------------------------------------------------------------------------


def test_dispatcher_allow_gate_send_proceeds():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=_AllowGate())

    run = _make_run()
    # This test covers gate logic, not send execution. Channel dispatch now goes
    # through the action registry (get_action_executor), so patch that seam to
    # return a fake executor that just advances to the next node.
    fake_executor_cls = MagicMock()
    fake_executor_cls.return_value.execute = AsyncMock(return_value="exit-1")
    with patch(
        "src.app.services.automation.step_dispatcher.get_action_executor",
        return_value=fake_executor_cls,
    ):
        result = asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    assert result.status == "completed"
    assert result.outcome == "sent"
    rt.fail_run.assert_not_awaited()
    rt.complete_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# Dispatcher: gate=block → run fails
# ---------------------------------------------------------------------------


def test_dispatcher_block_gate_fails_run():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=_BlockGate())

    run = _make_run()
    result = asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    assert result.status == "failed"
    rt.fail_step.assert_awaited_once()
    rt.fail_run.assert_awaited_once()
    rt.complete_run.assert_not_awaited()


def test_dispatcher_block_gate_result_code():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=_BlockGate())

    run = _make_run()
    asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    call_kwargs = rt.fail_step.call_args
    assert call_kwargs.kwargs.get("result_code") == "compliance_blocked"


# ---------------------------------------------------------------------------
# Dispatcher: gate=hold → run DEFERS (held to the next window, never dropped).
# Scope §8 fix: hold now schedules a resume timer + waits, rather than
# terminating the run with a compliance_hold outcome.
# ---------------------------------------------------------------------------


def test_dispatcher_hold_gate_defers_with_waiting_status():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=_HoldGate())

    run = _make_run()
    result = asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    # Held, not dropped: the run waits for a resume timer instead of completing.
    assert result.status == "waiting"
    rt.wait_run.assert_awaited_once()
    rt.complete_run.assert_not_awaited()
    rt.fail_run.assert_not_awaited()


def test_dispatcher_hold_gate_schedules_resume_timer():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=_HoldGate())

    run = _make_run()
    asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    # A resume timer is created and the send step is begun with a scheduled_at,
    # so on fire the run re-checks the gate at the same send node.
    sched.create_timer.assert_awaited_once()
    begin_kwargs = rt.begin_step.call_args.kwargs
    assert begin_kwargs.get("scheduled_at") is not None
    rt.fail_step.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dispatcher defaults to NoOp when no gate supplied
# ---------------------------------------------------------------------------


def test_dispatcher_defaults_to_noop_gate():
    session = _make_session()
    rt = _make_runtime()
    sched = AsyncMock()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    assert isinstance(dispatcher.gate, NoOpComplianceGate)

    run = _make_run()
    result = asyncio.run(dispatcher.advance(run, _sms_to_exit_definition(), context={}))

    assert result.status == "completed"
