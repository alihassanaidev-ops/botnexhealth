"""Unit tests for AutomationWorkflowSchedulerService timer operations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.app.models.automation_workflow import AutomationTimerStatus, AutomationWorkflowTimer
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService

_NOW = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)


def _make_session(*, timer_list: list | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = timer_list or []
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _make_timer(
    status: str = AutomationTimerStatus.PENDING.value,
    due_at: datetime | None = None,
    claim_expires_at: datetime | None = None,
) -> AutomationWorkflowTimer:
    timer = AutomationWorkflowTimer(
        institution_id="inst-1",
        workflow_run_id="run-1",
        due_at=due_at or _NOW,
        status=status,
    )
    if claim_expires_at is not None:
        timer.claim_expires_at = claim_expires_at
    return timer


def test_create_timer_sets_pending_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowSchedulerService(session)
    timer = asyncio.run(
        svc.create_timer(
            institution_id="inst-1",
            workflow_run_id="run-1",
            due_at=_NOW + timedelta(hours=24),
            timezone_name="America/Chicago",
        )
    )
    assert timer.status == AutomationTimerStatus.PENDING.value
    session.add.assert_called_once_with(timer)


def test_fire_timer_sets_fired_status() -> None:
    session = _make_session()
    svc = AutomationWorkflowSchedulerService(session)
    timer = _make_timer(AutomationTimerStatus.CLAIMED.value)
    asyncio.run(svc.fire_timer(timer))
    assert timer.status == AutomationTimerStatus.FIRED.value
    assert timer.fired_at is not None


def test_cancel_timers_for_run_cancels_pending_and_claimed() -> None:
    t1 = _make_timer(AutomationTimerStatus.PENDING.value)
    t1.id = "timer-1"
    t2 = _make_timer(AutomationTimerStatus.CLAIMED.value)
    t2.id = "timer-2"
    session = _make_session(timer_list=[t1, t2])
    svc = AutomationWorkflowSchedulerService(session)
    count = asyncio.run(svc.cancel_timers_for_run("run-1"))
    assert count == 2
    assert t1.status == AutomationTimerStatus.CANCELLED.value
    assert t2.status == AutomationTimerStatus.CANCELLED.value
    assert t1.cancelled_at is not None


def test_cancel_timers_for_run_returns_zero_when_none() -> None:
    session = _make_session(timer_list=[])
    svc = AutomationWorkflowSchedulerService(session)
    count = asyncio.run(svc.cancel_timers_for_run("run-no-timers"))
    assert count == 0
    session.flush.assert_not_awaited()


def test_claim_due_timers_stamps_claim_fields() -> None:
    timer = _make_timer(AutomationTimerStatus.PENDING.value, due_at=_NOW - timedelta(minutes=5))
    session = _make_session(timer_list=[timer])
    svc = AutomationWorkflowSchedulerService(session)
    claimed = asyncio.run(svc.claim_due_timers(now=_NOW))
    assert len(claimed) == 1
    assert claimed[0].status == AutomationTimerStatus.CLAIMED.value
    assert claimed[0].claim_token is not None
    assert claimed[0].claimed_at == _NOW
    assert claimed[0].claim_expires_at > _NOW


def test_recover_stale_claims_resets_expired() -> None:
    expired = _make_timer(
        AutomationTimerStatus.CLAIMED.value,
        claim_expires_at=_NOW - timedelta(seconds=1),
    )
    session = _make_session(timer_list=[expired])
    svc = AutomationWorkflowSchedulerService(session)
    count = asyncio.run(svc.recover_stale_claims(now=_NOW))
    assert count == 1
    assert expired.status == AutomationTimerStatus.PENDING.value
    assert expired.claim_token is None
    assert expired.claimed_at is None
    assert expired.claim_expires_at is None


def test_recover_stale_claims_ignores_valid_claims() -> None:
    valid = _make_timer(
        AutomationTimerStatus.CLAIMED.value,
        claim_expires_at=_NOW + timedelta(seconds=60),
    )
    session = _make_session(timer_list=[])
    svc = AutomationWorkflowSchedulerService(session)
    count = asyncio.run(svc.recover_stale_claims(now=_NOW))
    assert count == 0
    assert valid.status == AutomationTimerStatus.CLAIMED.value
