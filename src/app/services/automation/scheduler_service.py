"""Durable timer service: create, claim, fire, cancel, and recover stale timers."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationTimerStatus,
    AutomationWorkflowTimer,
)

logger = logging.getLogger(__name__)

_DEFAULT_CLAIM_TTL_SECONDS = 120
_DEFAULT_CLAIM_BATCH = 50

# How many extra rows to consider per claim so the fairness pass has a choice.
# Four batches is enough to interleave several busy tenants without making the
# locked read materially more expensive.
_FAIRNESS_OVERSCAN = 4


class AutomationWorkflowSchedulerService:
    """Creates, claims, fires, cancels, and recovers durable workflow timers.

    Timers are database rows; multiple workers can poll safely because claiming
    uses FOR UPDATE SKIP LOCKED so each timer is owned by exactly one worker.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_timer(
        self,
        *,
        institution_id: str,
        workflow_run_id: str,
        due_at: datetime,
        location_id: str | None = None,
        step_execution_id: str | None = None,
        due_local_at: datetime | None = None,
        timezone_name: str | None = None,
    ) -> AutomationWorkflowTimer:
        timer = AutomationWorkflowTimer(
            institution_id=institution_id,
            location_id=location_id,
            workflow_run_id=workflow_run_id,
            step_execution_id=step_execution_id,
            due_at=due_at,
            due_local_at=due_local_at,
            timezone=timezone_name,
            status=AutomationTimerStatus.PENDING.value,
        )
        self.session.add(timer)
        await self.session.flush()
        return timer

    async def claim_due_timers(
        self,
        *,
        now: datetime | None = None,
        limit: int = _DEFAULT_CLAIM_BATCH,
        claim_ttl_seconds: int = _DEFAULT_CLAIM_TTL_SECONDS,
    ) -> list[AutomationWorkflowTimer]:
        """Claim a batch of due pending timers for dispatch.

        Uses FOR UPDATE SKIP LOCKED so concurrent pollers each get a disjoint
        batch with no contention. Each claim expires after claim_ttl_seconds;
        recover_stale_claims() resets expired claims for re-processing.
        """
        now = now or datetime.now(tz=timezone.utc)
        claim_token = secrets.token_hex(16)
        claim_expires_at = now + timedelta(seconds=claim_ttl_seconds)

        stmt = (
            select(AutomationWorkflowTimer)
            .where(
                AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
                AutomationWorkflowTimer.due_at <= now,
            )
            .order_by(AutomationWorkflowTimer.due_at)
            # Over-fetch so the fairness pass has something to choose between.
            # Ordering by due_at alone means one tenant's bulk enrolment fills
            # the whole batch and every other clinic waits behind it.
            .limit(limit * _FAIRNESS_OVERSCAN)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        candidates = list(result.scalars().all())
        timers = _round_robin_by_institution(candidates, limit)

        for timer in timers:
            timer.status = AutomationTimerStatus.CLAIMED.value
            timer.claim_token = claim_token
            timer.claimed_at = now
            timer.claim_expires_at = claim_expires_at

        if timers:
            await self.session.flush()
        return timers

    async def count_due(self, *, now: datetime | None = None) -> int:
        """Pending timers already due.

        Exposed so the poller can report the backlog it did not get to, rather
        than leaving queue depth to be inferred from claim counts.
        """
        now = now or datetime.now(tz=timezone.utc)
        result = await self.session.execute(
            select(func.count())
            .select_from(AutomationWorkflowTimer)
            .where(
                AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
                AutomationWorkflowTimer.due_at <= now,
            )
        )
        return int(result.scalar_one() or 0)

    async def fire_timer(self, timer: AutomationWorkflowTimer) -> None:
        """Mark a claimed timer as fired. Caller dispatches the run step."""
        timer.status = AutomationTimerStatus.FIRED.value
        timer.fired_at = datetime.now(tz=timezone.utc)
        await self.session.flush()

    async def cancel_timers_for_run(self, workflow_run_id: str) -> int:
        """Cancel all pending/claimed timers for a run. Returns count cancelled."""
        result = await self.session.execute(
            select(AutomationWorkflowTimer).where(
                AutomationWorkflowTimer.workflow_run_id == workflow_run_id,
                AutomationWorkflowTimer.status.in_([
                    AutomationTimerStatus.PENDING.value,
                    AutomationTimerStatus.CLAIMED.value,
                ]),
            )
        )
        timers = list(result.scalars().all())
        now = datetime.now(tz=timezone.utc)
        for timer in timers:
            timer.status = AutomationTimerStatus.CANCELLED.value
            timer.cancelled_at = now
        if timers:
            await self.session.flush()
        return len(timers)

    async def reschedule_timer(
        self, timer: AutomationWorkflowTimer, *, due_at: datetime
    ) -> AutomationWorkflowTimer:
        """Return a claimed timer to pending with a new due time (defer dispatch).

        Used when a run cannot advance yet but must not be dropped — e.g. its
        workflow is paused: the timer is re-armed for a later poll rather than
        fired/cancelled, so the run resumes once the workflow is active again.
        """
        timer.status = AutomationTimerStatus.PENDING.value
        timer.due_at = due_at
        timer.claim_token = None
        timer.claimed_at = None
        timer.claim_expires_at = None
        await self.session.flush()
        return timer

    async def recover_stale_claims(
        self, *, now: datetime | None = None
    ) -> int:
        """Reset claimed-but-expired timers back to pending for the next poll cycle."""
        now = now or datetime.now(tz=timezone.utc)
        result = await self.session.execute(
            select(AutomationWorkflowTimer).where(
                AutomationWorkflowTimer.status == AutomationTimerStatus.CLAIMED.value,
                AutomationWorkflowTimer.claim_expires_at <= now,
            )
        )
        timers = list(result.scalars().all())
        for timer in timers:
            timer.status = AutomationTimerStatus.PENDING.value
            timer.claim_token = None
            timer.claimed_at = None
            timer.claim_expires_at = None
        if timers:
            await self.session.flush()
        return len(timers)


def _round_robin_by_institution(
    candidates: list[AutomationWorkflowTimer], limit: int
) -> list[AutomationWorkflowTimer]:
    """Interleave due timers across institutions, oldest-first within each.

    Without this, ``ORDER BY due_at`` alone hands the whole batch to whichever
    tenant enqueued the most work — a 500-patient recall blast occupies every
    claim cycle, and every other clinic's reminders sit behind it with nothing
    in the logs to explain why they are late.

    Deals one timer per institution per pass, so a tenant with a backlog still
    progresses, just never at the cost of starving anyone else. Fairness must
    not become a per-tenant rate limit: when one tenant is the only one with
    work, it takes the whole batch.
    """
    if len(candidates) <= limit:
        return candidates

    by_institution: dict[str, list[AutomationWorkflowTimer]] = {}
    for timer in candidates:
        by_institution.setdefault(str(timer.institution_id), []).append(timer)

    # Deal to the tenant whose longest-waiting work is oldest. Candidates
    # arrive in due_at order, so each queue is already oldest-first.
    queues = sorted(by_institution.values(), key=lambda group: group[0].due_at)

    selected: list[AutomationWorkflowTimer] = []
    cursor = 0
    while len(selected) < limit:
        progressed = False
        for queue in queues:
            if cursor >= len(queue):
                continue
            selected.append(queue[cursor])
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
        cursor += 1
    return selected
