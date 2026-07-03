"""Unit tests for automation workflow Celery task helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationTimerStatus,
    AutomationWorkflowRun,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.tasks.automation_workflow import (
    _claim_and_enqueue_async,
    _dispatch_timer_async,
    _retry_countdown,
)

_NOW = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)

_VALID_DEFINITION = {
    "trigger": {"type": "manual"},
    "entry_node_id": "exit-1",
    "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
}


# ---------------------------------------------------------------------------
# _retry_countdown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retries,expected", [
    (0, 1),
    (1, 2),
    (2, 4),
    (3, 8),
    (8, 256),
    (9, 300),  # capped at 300
])
def test_retry_countdown(retries, expected) -> None:
    assert _retry_countdown(retries) == expected


# ---------------------------------------------------------------------------
# _claim_and_enqueue_async
# ---------------------------------------------------------------------------


def _make_timer(timer_id="t-1", institution_id="inst-1", location_id=None, run_id="run-1"):
    t = MagicMock()
    t.id = timer_id
    t.institution_id = institution_id
    t.location_id = location_id
    t.workflow_run_id = run_id
    return t


@pytest.mark.asyncio
async def test_claim_and_enqueue_no_timers() -> None:
    mock_svc = AsyncMock()
    mock_svc.claim_due_timers = AsyncMock(return_value=[])
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.tasks.automation_workflow.dispatch_workflow_timer",
        ) as mock_dispatch,
    ):
        result = await _claim_and_enqueue_async()

    assert result == {"claimed": 0}
    mock_dispatch.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_claim_and_enqueue_enqueues_per_timer() -> None:
    timers = [
        _make_timer("t-1", "inst-1", None, "run-1"),
        _make_timer("t-2", "inst-2", "loc-1", "run-2"),
    ]
    mock_svc = AsyncMock()
    mock_svc.claim_due_timers = AsyncMock(return_value=timers)
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.tasks.automation_workflow.dispatch_workflow_timer",
        ) as mock_dispatch,
    ):
        result = await _claim_and_enqueue_async()

    assert result == {"claimed": 2}
    assert mock_dispatch.apply_async.call_count == 2


# ---------------------------------------------------------------------------
# _dispatch_timer_async — timer not found / not claimed
# ---------------------------------------------------------------------------


def _mock_session_get(return_map: dict):
    """Build an AsyncSession where session.get(Model, pk) returns from return_map."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    async def _get(model, pk, **kwargs):
        return return_map.get((model, pk))

    session.get = _get

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    return session


@pytest.mark.asyncio
async def test_dispatch_timer_not_found_skips() -> None:
    session = _mock_session_get({})

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=session,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "timer not claimed" in result["reason"]


@pytest.mark.asyncio
async def test_dispatch_timer_run_not_advanceable_fires_timer() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.COMPLETED.value  # terminal — not advanceable

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
    })

    mock_sched = AsyncMock()
    mock_sched.fire_timer = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_sched,
        ),
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "not advanceable" in result["reason"]
    mock_sched.fire_timer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_timer_version_missing_skips() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_version_id = "ver-1"
    run.location_id = None

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        # version deliberately absent
    })

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=session,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "version not found" in result["reason"]


@pytest.mark.asyncio
async def test_dispatch_timer_happy_path_returns_dispatch_result() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_version_id = "ver-1"
    run.location_id = None
    run.trigger_metadata = {}
    run.current_step_id = "wait-1"

    version = MagicMock()
    version.definition = _VALID_DEFINITION

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        (AutomationWorkflowVersion, "ver-1"): version,
    })

    from src.app.services.automation.step_dispatcher import DispatchResult

    mock_dispatcher = AsyncMock()
    mock_dispatcher.scheduler = AsyncMock()
    mock_dispatcher.scheduler.fire_timer = AsyncMock()
    mock_dispatcher.resume_after_timer = AsyncMock(
        return_value=DispatchResult(status="completed", outcome="done", steps_advanced=1)
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.build_dispatcher",
            new=AsyncMock(return_value=(mock_dispatcher, "UTC")),
        ),
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["dispatch_status"] == "completed"
    assert result["outcome"] == "done"
    assert result["steps_advanced"] == 1
    mock_dispatcher.scheduler.fire_timer.assert_awaited_once()
    mock_dispatcher.resume_after_timer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_timer_defers_when_workflow_paused() -> None:
    """A waiting run whose workflow is paused is deferred (timer re-armed), not
    advanced — pause stops in-flight runs, not just new enrollments."""
    from src.app.models.automation_workflow import (
        AutomationWorkflow,
        AutomationWorkflowStatus,
    )

    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_id = "wf-1"
    run.location_id = None

    workflow = MagicMock()
    workflow.status = AutomationWorkflowStatus.PAUSED.value

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        (AutomationWorkflow, "wf-1"): workflow,
    })

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch("src.app.tasks.automation_workflow.build_dispatcher") as mock_build,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1", institution_id="inst-1", location_id=None, run_id="run-1"
        )

    assert result["skipped"] is True
    assert result["reason"] == "workflow paused"
    assert result.get("deferred") is True
    mock_build.assert_not_called()
    assert timer.status == AutomationTimerStatus.PENDING.value  # re-armed, not fired


@pytest.mark.asyncio
async def test_recover_stale_async_returns_count() -> None:
    """The stale-claim recovery task delegates to the scheduler and reports count."""
    from src.app.tasks.automation_workflow import _recover_stale_async

    session = _mock_session_get({})
    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService"
        ) as mock_cls,
    ):
        mock_cls.return_value.recover_stale_claims = AsyncMock(return_value=3)
        result = await _recover_stale_async()

    assert result["recovered"] == 3
    mock_cls.return_value.recover_stale_claims.assert_awaited_once()
