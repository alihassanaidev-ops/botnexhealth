"""Run state machine and step execution records for the workflow engine."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
)

logger = logging.getLogger(__name__)

# Result codes that mark a send step as having actually dispatched to the vendor.
# A completed step with one of these means the patient was already contacted for
# this (run, step) — the idempotency guard skips a second send.
SEND_SUCCESS_RESULT_CODES = frozenset({"sent", "call_placed"})

_TERMINAL_RUN_STATUSES = frozenset({
    AutomationRunStatus.COMPLETED.value,
    AutomationRunStatus.CANCELLED.value,
    AutomationRunStatus.FAILED.value,
})


class AutomationWorkflowRuntimeService:
    """Controls run state transitions and creates step execution records.

    Does not send messages. Channel dispatch belongs to action handlers that
    will be registered against this service in a later slice.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start_run(self, run: AutomationWorkflowRun) -> AutomationWorkflowRun:
        if run.status != AutomationRunStatus.PENDING.value:
            raise ValueError(
                f"Cannot start run {run.id}: expected 'pending', got '{run.status}'"
            )
        run.status = AutomationRunStatus.RUNNING.value
        run.started_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        await self._emit(run, "run.started")
        return run

    async def begin_step(
        self,
        run: AutomationWorkflowRun,
        *,
        step_id: str,
        step_type: str,
        attempt_number: int | None = None,
        max_attempts: int = 1,
        scheduled_at: datetime | None = None,
        scheduled_local_at: datetime | None = None,
        scheduled_timezone: str | None = None,
    ) -> AutomationWorkflowStepExecution:
        """Create a step execution record and advance the run's current step pointer.

        When ``attempt_number`` is not given it is allocated as the next attempt for
        this ``(run, step_id)`` (``max(existing)+1``). A node can legitimately be
        begun more than once — e.g. a quiet-hours *hold* creates the step, then on
        resume the send executor begins it again, and a retry begins another attempt.
        Auto-incrementing avoids colliding on the ``(run, step, attempt)`` unique
        index and records each attempt distinctly.
        """
        if attempt_number is None:
            max_existing = (
                await self.session.execute(
                    select(func.max(AutomationWorkflowStepExecution.attempt_number)).where(
                        AutomationWorkflowStepExecution.workflow_run_id == run.id,
                        AutomationWorkflowStepExecution.step_id == step_id,
                    )
                )
            ).scalar_one_or_none()
            attempt_number = (max_existing or 0) + 1
        step = AutomationWorkflowStepExecution(
            institution_id=run.institution_id,
            location_id=run.location_id,
            workflow_run_id=run.id,
            workflow_version_id=run.workflow_version_id,
            step_id=step_id,
            step_type=step_type,
            attempt_number=attempt_number,
            max_attempts=max_attempts,
            scheduled_at=scheduled_at,
            scheduled_local_at=scheduled_local_at,
            scheduled_timezone=scheduled_timezone,
            status=AutomationStepStatus.PENDING.value,
        )
        self.session.add(step)
        run.current_step_id = step_id
        await self.session.flush()
        await self._emit(run, "step.started", step_id=step_id, metadata={"step_type": step_type})
        return step

    async def already_sent(self, run: AutomationWorkflowRun, step_id: str) -> bool:
        """True if a send for this ``(run, step_id)`` has already dispatched.

        Send-time idempotency (scope §5.4 exactly-one-action): a task redelivery,
        a re-advance, or a quiet-hours hold→resume must not contact the patient a
        second time. Keyed on a COMPLETED step with a send-success result code
        (``sent``/``call_placed``) — a resumed *hold* step is COMPLETED with no
        result code, so it does not count as already-sent.
        """
        row = (
            await self.session.execute(
                select(AutomationWorkflowStepExecution.id)
                .where(
                    AutomationWorkflowStepExecution.workflow_run_id == run.id,
                    AutomationWorkflowStepExecution.step_id == step_id,
                    AutomationWorkflowStepExecution.status
                    == AutomationStepStatus.COMPLETED.value,
                    AutomationWorkflowStepExecution.result_code.in_(
                        tuple(SEND_SUCCESS_RESULT_CODES)
                    ),
                )
                .limit(1)
            )
        ).first()
        return row is not None

    async def complete_step(
        self,
        step: AutomationWorkflowStepExecution,
        *,
        result_code: str | None = None,
        result_metadata: dict | None = None,
    ) -> AutomationWorkflowStepExecution:
        step.status = AutomationStepStatus.COMPLETED.value
        step.result_code = result_code
        step.result_metadata = result_metadata
        step.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        return step

    async def mark_step_awaiting_outcome(
        self,
        step: AutomationWorkflowStepExecution,
        *,
        result_code: str | None = None,
        result_metadata: dict | None = None,
    ) -> AutomationWorkflowStepExecution:
        """Record a placed call's result_code + metadata (e.g. retell_call_id) on a
        step WITHOUT completing it — the run then parks WAITING (via wait_run) for the
        outcome webhook. The marker lets resume tell a placed-and-parked voice step
        (advance past) from a quiet-hours hold step (re-run the gate)."""
        step.result_code = result_code
        step.result_metadata = result_metadata
        await self.session.flush()
        return step

    async def fail_step(
        self,
        step: AutomationWorkflowStepExecution,
        *,
        error_message: str | None = None,
        result_code: str | None = None,
    ) -> AutomationWorkflowStepExecution:
        step.status = AutomationStepStatus.FAILED.value
        step.error_message = error_message
        step.result_code = result_code
        step.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        return step

    async def wait_run(
        self,
        run: AutomationWorkflowRun,
        step: AutomationWorkflowStepExecution,
    ) -> None:
        """Transition run and current step to waiting (timer has been set)."""
        run.status = AutomationRunStatus.WAITING.value
        step.status = AutomationStepStatus.WAITING.value
        await self.session.flush()
        await self._emit(run, "run.waiting", step_id=step.step_id)

    async def resume_run(
        self,
        run: AutomationWorkflowRun,
        step: AutomationWorkflowStepExecution,
    ) -> None:
        """Transition a waiting run back to running after its timer fires."""
        if run.status != AutomationRunStatus.WAITING.value:
            raise ValueError(
                f"Cannot resume run {run.id}: expected 'waiting', got '{run.status}'"
            )
        run.status = AutomationRunStatus.RUNNING.value
        step.status = AutomationStepStatus.COMPLETED.value
        step.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        await self._emit(run, "run.resumed", step_id=step.step_id)

    async def complete_run(
        self,
        run: AutomationWorkflowRun,
        *,
        outcome: str | None = None,
    ) -> AutomationWorkflowRun:
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        run.status = AutomationRunStatus.COMPLETED.value
        run.outcome = outcome
        run.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        await self._emit(run, "run.completed", metadata={"outcome": outcome})
        return run

    async def fail_run(
        self,
        run: AutomationWorkflowRun,
        *,
        reason: str | None = None,
    ) -> AutomationWorkflowRun:
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        run.status = AutomationRunStatus.FAILED.value
        run.blocked_reason = reason
        run.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        await self._emit(run, "run.failed", metadata={"reason": reason})
        return run

    async def _emit(
        self,
        run: AutomationWorkflowRun,
        event_type: str,
        *,
        step_id: str | None = None,
        metadata: dict | None = None,
    ) -> AutomationWorkflowEvent:
        event = AutomationWorkflowEvent(
            institution_id=run.institution_id,
            location_id=run.location_id,
            workflow_run_id=run.id,
            event_type=event_type,
            step_id=step_id,
            event_metadata=metadata,
        )
        self.session.add(event)
        await self.session.flush()

        # Best-effort SSE progress hint on run-level lifecycle transitions. PHI-free
        # (no run detail) — the campaign progress UI (Plan 02/08) refetches on receipt.
        if event_type.startswith("run."):
            try:
                from src.app.services.event_bus import publish_event

                publish_event(run.institution_id, "workflow_run_updated")
            except Exception:  # noqa: BLE001 — SSE is a non-critical hint
                logger.debug("workflow_run_updated SSE publish failed", exc_info=True)

        return event
