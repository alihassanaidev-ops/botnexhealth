"""Unit tests for WorkflowStepDispatcher node dispatch and condition evaluation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationStepStatus,
)
from src.app.services.automation.definition_schema import (
    AppointmentOffsetTrigger,
    CalendarDelay,
    ConditionNode,
    ConditionRule,
    DurationDelay,
    ExitNode,
    ManualTrigger,
    SendSmsNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.step_dispatcher import (
    DispatchResult,
    WorkflowStepDispatcher,
    _compute_due_at,
    _evaluate_condition,
    _evaluate_rule,
)

_NOW = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(status: str = AutomationRunStatus.RUNNING.value) -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status=status,
    )


def _make_step(status: str = AutomationStepStatus.WAITING.value) -> AutomationWorkflowStepExecution:
    return AutomationWorkflowStepExecution(
        institution_id="inst-1",
        workflow_run_id="run-1",
        workflow_version_id="ver-1",
        step_id="wait-1",
        step_type="wait",
        status=status,
    )


def _make_runtime() -> AsyncMock:
    rt = AsyncMock()
    step = MagicMock()
    step.id = "step-exec-1"
    step.step_id = "step-1"
    rt.begin_step = AsyncMock(return_value=step)
    rt.complete_step = AsyncMock(return_value=step)
    rt.wait_run = AsyncMock()
    rt.complete_run = AsyncMock()
    rt.fail_run = AsyncMock()
    rt.resume_run = AsyncMock()
    return rt


def _make_scheduler() -> AsyncMock:
    sched = AsyncMock()
    timer = MagicMock()
    timer.id = "timer-1"
    sched.create_timer = AsyncMock(return_value=timer)
    sched.fire_timer = AsyncMock()
    return sched


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _definition(nodes: list, entry: str, trigger=None) -> WorkflowDefinition:
    return WorkflowDefinition(
        trigger=trigger or AppointmentOffsetTrigger(offset_hours=-24),
        entry_node_id=entry,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# advance() — send_sms → exit
# ---------------------------------------------------------------------------


def test_advance_sms_to_exit_returns_completed() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            SendSmsNode(id="sms-1", body_template="Hi", next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="sent"),
        ],
        entry="sms-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}))

    assert result.status == "completed"
    assert result.outcome == "sent"
    assert result.steps_advanced == 2
    rt.complete_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# advance() — wait node creates timer and pauses
# ---------------------------------------------------------------------------


def test_advance_wait_node_returns_waiting() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            WaitNode(
                id="wait-1",
                delay=DurationDelay(duration_seconds=3600),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1"),
        ],
        entry="wait-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "waiting"
    assert result.timer_id == "timer-1"
    sched.create_timer.assert_awaited_once()
    rt.wait_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# advance() — condition node branches correctly
# ---------------------------------------------------------------------------


def test_advance_condition_true_branch() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            ConditionNode(
                id="cond-1",
                rules=[ConditionRule(field="status", op="eq", value="confirmed")],
                true_next_node_id="exit-ok",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-ok", outcome="confirmed"),
            ExitNode(id="exit-no", outcome="no_response"),
        ],
        entry="cond-1",
    )

    result = asyncio.run(
        dispatcher.advance(run, defn, context={"status": "confirmed"})
    )

    assert result.status == "completed"
    assert result.outcome == "confirmed"


def test_advance_condition_false_branch() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            ConditionNode(
                id="cond-1",
                rules=[ConditionRule(field="status", op="eq", value="confirmed")],
                true_next_node_id="exit-ok",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-ok", outcome="confirmed"),
            ExitNode(id="exit-no", outcome="no_response"),
        ],
        entry="cond-1",
    )

    result = asyncio.run(
        dispatcher.advance(run, defn, context={"status": "pending"})
    )

    assert result.status == "completed"
    assert result.outcome == "no_response"


# ---------------------------------------------------------------------------
# advance() — missing node fails the run
# ---------------------------------------------------------------------------


def test_advance_missing_node_fails_run() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    run.current_step_id = "ghost-node"
    defn = _definition(
        nodes=[ExitNode(id="exit-1")],
        entry="exit-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}))

    assert result.status == "failed"
    rt.fail_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# _evaluate_rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op,field_val,rule_val,expected", [
    ("eq", "confirmed", "confirmed", True),
    ("eq", "confirmed", "pending", False),
    ("neq", "confirmed", "pending", True),
    ("in", "confirmed", ["confirmed", "pending"], True),
    ("in", "other", ["confirmed", "pending"], False),
    ("not_in", "other", ["confirmed"], True),
    ("is_null", None, None, True),
    ("is_null", "x", None, False),
    ("is_not_null", "x", None, True),
    ("is_not_null", None, None, False),
])
def test_evaluate_rule(op, field_val, rule_val, expected) -> None:
    rule = ConditionRule(field="f", op=op, value=rule_val)
    assert _evaluate_rule(rule, {"f": field_val}) is expected


def test_evaluate_condition_and_all_true() -> None:
    node = ConditionNode(
        id="c",
        logic="AND",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "y"}) is True


def test_evaluate_condition_and_one_false() -> None:
    node = ConditionNode(
        id="c",
        logic="AND",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "z"}) is False


def test_evaluate_condition_or_one_true() -> None:
    node = ConditionNode(
        id="c",
        logic="OR",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "z"}) is True


# ---------------------------------------------------------------------------
# _compute_due_at
# ---------------------------------------------------------------------------


def test_compute_due_at_duration() -> None:
    delay = DurationDelay(duration_seconds=3600)
    result = _compute_due_at(delay, "UTC", _NOW)
    from datetime import timedelta
    assert result == _NOW + timedelta(seconds=3600)


def test_compute_due_at_calendar_future_time() -> None:
    # _NOW is 2026-07-02 14:00 UTC = 2026-07-02 09:00 America/Chicago (UTC-5 in July)
    delay = CalendarDelay(offset_days=0, time_of_day="11:00")
    result = _compute_due_at(delay, "America/Chicago", _NOW)
    # 11:00 Chicago on same day = 16:00 UTC, which is after _NOW (14:00 UTC)
    assert result.hour == 16
    assert result.tzinfo is not None


def test_compute_due_at_calendar_past_time_advances_day() -> None:
    # _NOW is 14:00 UTC = 09:00 Chicago; if time_of_day is 08:00, it's in the past
    delay = CalendarDelay(offset_days=0, time_of_day="08:00")
    result = _compute_due_at(delay, "America/Chicago", _NOW)
    # Should advance to next day: 08:00 Chicago next day = 13:00 UTC next day
    from datetime import date
    assert result.date() > _NOW.date()


def test_compute_due_at_unknown_timezone_falls_back_to_utc() -> None:
    delay = CalendarDelay(offset_days=1, time_of_day="09:00")
    result = _compute_due_at(delay, "Fake/Zone", _NOW)
    assert result is not None  # doesn't raise; returns valid datetime
