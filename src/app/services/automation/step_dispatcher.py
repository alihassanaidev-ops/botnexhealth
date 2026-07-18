"""Workflow step dispatcher: advances a run through its definition until wait or exit.

All send channels are live via the action registry: SMS (Plan 04), email (Plan 05),
and voice (Plan 03). `_dispatch_send_stub` remains only as a defensive no-op for any
unregistered send node type.

Use ``build_dispatcher()`` to construct a dispatcher: it is the single wiring point
that injects the real ComplianceGateService and resolves the location timezone, so
no caller can accidentally send without a compliance gate or in the wrong timezone.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import (
    CalendarDelay,
    ConditionNode,
    ConditionRule,
    DurationDelay,
    ExitNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.action_registry import get_action_executor
from src.app.services.automation.compliance_gate import ComplianceGate, NoOpComplianceGate
from src.app.services.automation.revalidation import NoOpRevalidator, RunRevalidator
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.voice_node_executor import (
    _CALL_PLACED_AWAITING,
    VoiceParked,
)

logger = logging.getLogger(__name__)

_MAX_STEPS = 50
# Spread of jitter applied to calendar (fixed local-time) sends so an 800-patient
# "9 AM reminder" batch doesn't hit the vendor in one burst. Full budget-aware
# pacing across NexHealth/Retell/Twilio is coordinated with Plans 09/11.
_DEFAULT_CALENDAR_JITTER_SECONDS = 300


@dataclass
class DispatchResult:
    status: Literal["waiting", "completed", "failed"]
    timer_id: str | None = None
    outcome: str | None = None
    steps_advanced: int = 0


class WorkflowStepDispatcher:
    """Advance a run through its definition nodes until a wait or exit is reached.

    Call advance() when a run starts, or after a timer fires (resume_after_timer).
    The caller is responsible for committing the session.
    """

    def __init__(
        self,
        session: AsyncSession,
        runtime: AutomationWorkflowRuntimeService,
        scheduler: AutomationWorkflowSchedulerService,
        gate: ComplianceGate | None = None,
        revalidator: RunRevalidator | None = None,
        calendar_jitter_seconds: int = 0,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.scheduler = scheduler
        self.gate: ComplianceGate = gate or NoOpComplianceGate()
        self.revalidator: RunRevalidator = revalidator or NoOpRevalidator()
        # 0 = deterministic (unit tests); build_dispatcher sets a production spread.
        self.calendar_jitter_seconds = calendar_jitter_seconds

    async def advance(
        self,
        run: AutomationWorkflowRun,
        definition: WorkflowDefinition,
        *,
        context: dict,
        location_timezone: str = "UTC",
        now: datetime | None = None,
    ) -> DispatchResult:
        """Advance run from current_step_id (or entry node) until wait or exit."""
        now = now or datetime.now(tz=timezone.utc)
        context = {**(run.trigger_metadata or {}), **(context or {})}
        node_map = {n.id: n for n in definition.nodes}
        current_node_id = run.current_step_id or definition.entry_node_id
        steps_advanced = 0

        while steps_advanced < _MAX_STEPS:
            node = node_map.get(current_node_id)
            if node is None:
                logger.error(
                    "dispatch: node not found institution=%s run=%s node=%s",
                    run.institution_id, run.id, current_node_id,
                )
                await self.runtime.fail_run(run, reason=f"node '{current_node_id}' not found")
                return DispatchResult(status="failed", steps_advanced=steps_advanced)

            steps_advanced += 1

            if isinstance(node, WaitNode):
                due_at = _compute_due_at(node.delay, location_timezone, now)
                # Smooth calendar (fixed local-time) sends to avoid vendor stampedes.
                if self.calendar_jitter_seconds and isinstance(node.delay, CalendarDelay):
                    due_at += timedelta(
                        seconds=secrets.randbelow(self.calendar_jitter_seconds + 1)
                    )
                step = await self.runtime.begin_step(
                    run,
                    step_id=node.id,
                    step_type="wait",
                    scheduled_at=due_at,
                    scheduled_timezone=location_timezone,
                )
                timer = await self.scheduler.create_timer(
                    institution_id=run.institution_id,
                    location_id=run.location_id,
                    workflow_run_id=run.id,
                    step_execution_id=step.id,
                    due_at=due_at,
                    timezone_name=location_timezone,
                )
                await self.runtime.wait_run(run, step)
                return DispatchResult(
                    status="waiting", timer_id=timer.id, steps_advanced=steps_advanced
                )

            elif isinstance(node, (SendSmsNode, SendVoiceNode, SendEmailNode)):
                # Dispatch-time revalidation: the appointment/state this run targets
                # may have changed since enrollment (e.g. cancelled). Skip + exit if
                # the run is no longer valid, before spending a send.
                skip_outcome = await self.revalidator.revalidate(run)
                if skip_outcome is not None:
                    step = await self.runtime.begin_step(
                        run, step_id=node.id, step_type=node.type
                    )
                    await self.runtime.complete_step(step, result_code=skip_outcome)
                    await self.runtime.complete_run(run, outcome=skip_outcome)
                    logger.info(
                        "dispatch: revalidation skip run=%s node=%s outcome=%s",
                        run.id, node.id, skip_outcome,
                    )
                    return DispatchResult(
                        status="completed", outcome=skip_outcome, steps_advanced=steps_advanced
                    )

                content_class = (
                    definition.compliance.content_class if definition.compliance else None
                )
                gate_result = await self.gate.check(run, node.type, content_class=content_class)
                if gate_result.action == "block":
                    step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)
                    await self.runtime.fail_step(step, result_code="compliance_blocked")
                    await self.runtime.fail_run(run, reason=gate_result.reason or "compliance_blocked")
                    return DispatchResult(status="failed", steps_advanced=steps_advanced)
                if gate_result.action == "hold":
                    # Defer the send to the next permitted window instead of
                    # dropping it (scope §8: held, never dropped). Schedule a timer
                    # at retry_at; on fire the run resumes and re-checks the gate at
                    # this same send node.
                    resume_at = gate_result.retry_at or (now + timedelta(hours=1))
                    step = await self.runtime.begin_step(
                        run,
                        step_id=node.id,
                        step_type=node.type,
                        scheduled_at=resume_at,
                        scheduled_timezone=location_timezone,
                    )
                    timer = await self.scheduler.create_timer(
                        institution_id=run.institution_id,
                        location_id=run.location_id,
                        workflow_run_id=run.id,
                        step_execution_id=step.id,
                        due_at=resume_at,
                        timezone_name=location_timezone,
                    )
                    await self.runtime.wait_run(run, step)
                    logger.info(
                        "dispatch: hold->deferred run=%s node=%s resume_at=%s reason=%s",
                        run.id, node.id, resume_at, gate_result.reason,
                    )
                    return DispatchResult(
                        status="waiting", timer_id=timer.id, steps_advanced=steps_advanced
                    )
                # Channel dispatch via the action registry — new channels plug in
                # by registering an executor (see action_registry). Any unregistered
                # send type falls back to the defensive stub.
                executor_cls = get_action_executor(node.type)
                if executor_cls is None:
                    current_node_id = await self._dispatch_send_stub(run, node)
                else:
                    dispatch_result = await executor_cls(
                        self.session, self.runtime
                    ).execute(run, node, context)
                    if isinstance(dispatch_result, VoiceParked):
                        # Voice node placed a call and is parking for its outcome
                        # webhook. Set a safety-timeout timer so a never-arriving
                        # webhook can't hang the run, then wait; the webhook (or the
                        # timer) resumes via resume_after_timer.
                        resume_at = now + timedelta(minutes=dispatch_result.timeout_minutes)
                        timer = await self.scheduler.create_timer(
                            institution_id=run.institution_id,
                            location_id=run.location_id,
                            workflow_run_id=run.id,
                            step_execution_id=dispatch_result.step.id,
                            due_at=resume_at,
                            timezone_name=location_timezone,
                        )
                        await self.runtime.wait_run(run, dispatch_result.step)
                        logger.info(
                            "dispatch: voice parked for outcome run=%s node=%s timeout_at=%s",
                            run.id, node.id, resume_at,
                        )
                        return DispatchResult(
                            status="waiting", timer_id=timer.id, steps_advanced=steps_advanced
                        )
                    current_node_id = dispatch_result

            elif isinstance(node, ConditionNode):
                branch = _evaluate_condition(node, context)
                step = await self.runtime.begin_step(run, step_id=node.id, step_type="condition")
                await self.runtime.complete_step(
                    step, result_code=f"branch_{'true' if branch else 'false'}"
                )
                current_node_id = (
                    node.true_next_node_id if branch else node.false_next_node_id
                )

            elif isinstance(node, ExitNode):
                step = await self.runtime.begin_step(run, step_id=node.id, step_type="exit")
                await self.runtime.complete_step(step, result_code=node.outcome or "exit")
                await self.runtime.complete_run(run, outcome=node.outcome)
                return DispatchResult(
                    status="completed", outcome=node.outcome, steps_advanced=steps_advanced
                )

        logger.error(
            "dispatch: max step limit institution=%s run=%s", run.institution_id, run.id
        )
        await self.runtime.fail_run(run, reason="max step limit exceeded")
        return DispatchResult(status="failed", steps_advanced=steps_advanced)

    async def resume_after_timer(
        self,
        run: AutomationWorkflowRun,
        definition: WorkflowDefinition,
        *,
        context: dict,
        location_timezone: str = "UTC",
        now: datetime | None = None,
    ) -> DispatchResult:
        """Resume a WAITING run after its timer fires, then continue advancing.

        Two kinds of waits resume here:
          * a WaitNode delay — advance the step pointer past the wait node;
          * a compliance *hold* deferred at a send node — leave the pointer on the
            send node so advance() re-checks the gate and (if now permitted) sends.
        Finds the waiting step execution, resumes the run, repositions the pointer
        accordingly, then calls advance().
        """
        from sqlalchemy import select

        from src.app.models.automation_workflow import (
            AutomationRunStatus,
            AutomationStepStatus,
            AutomationWorkflowStepExecution,
        )

        if run.status != AutomationRunStatus.WAITING.value:
            logger.warning(
                "resume_after_timer: run %s not in waiting state (status=%s)",
                run.id, run.status,
            )
            return DispatchResult(status="failed")

        node_map = {n.id: n for n in definition.nodes}
        current_node = node_map.get(run.current_step_id or "")
        is_wait = isinstance(current_node, WaitNode)
        is_held_send = isinstance(current_node, (SendSmsNode, SendVoiceNode, SendEmailNode))
        if not (is_wait or is_held_send):
            await self.runtime.fail_run(
                run, reason=f"expected wait or held send node at '{run.current_step_id}'"
            )
            return DispatchResult(status="failed")

        result = await self.session.execute(
            select(AutomationWorkflowStepExecution)
            .where(
                AutomationWorkflowStepExecution.workflow_run_id == run.id,
                AutomationWorkflowStepExecution.step_id == run.current_step_id,
                AutomationWorkflowStepExecution.status == AutomationStepStatus.WAITING.value,
            )
            .order_by(AutomationWorkflowStepExecution.created_at.desc())
            .limit(1)
        )
        waiting_step = result.scalar_one_or_none()
        if waiting_step is None:
            await self.runtime.fail_run(
                run, reason=f"no waiting step execution for node '{run.current_step_id}'"
            )
            return DispatchResult(status="failed")

        await self.runtime.resume_run(run, waiting_step)
        is_parked_voice = is_held_send and waiting_step.result_code == _CALL_PLACED_AWAITING
        if is_wait or is_parked_voice:
            # WaitNode: move past the wait. Parked voice: the call already went out,
            # so advance PAST the send node (never re-dial) into whatever follows —
            # typically a ConditionNode that branches on `call_outcome`.
            run.current_step_id = current_node.next_node_id
            if is_parked_voice and "call_outcome" not in context:
                # Safety-timeout fired before any outcome webhook arrived → treat as
                # no outcome so a downstream branch can route it (e.g. retry/exit).
                context = {**context, "call_outcome": "timeout"}
        # else: a genuine quiet-hours held send stays put so advance() re-runs the gate.
        await self.session.flush()

        return await self.advance(
            run, definition, context=context, location_timezone=location_timezone, now=now
        )

    async def _dispatch_send_stub(
        self,
        run: AutomationWorkflowRun,
        node: SendSmsNode | SendVoiceNode | SendEmailNode,
    ) -> str:
        """Stub: records intent without sending. Real handlers wired in Plans 03/04/05."""
        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)
        logger.info(
            "stub dispatch: institution=%s run=%s step=%s type=%s",
            run.institution_id, run.id, node.id, node.type,
        )
        await self.runtime.complete_step(step, result_code="stub_dispatched")
        return node.next_node_id


def _evaluate_condition(node: ConditionNode, context: dict) -> bool:
    results = [_evaluate_rule(rule, context) for rule in node.rules]
    return all(results) if node.logic == "AND" else any(results)


def _evaluate_rule(rule: ConditionRule, context: dict) -> bool:
    value = context.get(rule.field)
    if rule.op == "eq":
        return value == rule.value
    if rule.op == "neq":
        return value != rule.value
    if rule.op == "in":
        return value in (rule.value or [])
    if rule.op == "not_in":
        return value not in (rule.value or [])
    if rule.op == "is_null":
        return value is None
    if rule.op == "is_not_null":
        return value is not None
    return False


def _compute_due_at(
    delay: DurationDelay | CalendarDelay,
    location_timezone: str,
    now: datetime,
) -> datetime:
    if isinstance(delay, DurationDelay):
        return now + timedelta(seconds=delay.duration_seconds)

    try:
        tz = ZoneInfo(location_timezone)
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning("unknown timezone '%s', falling back to UTC", location_timezone)
        tz = ZoneInfo("UTC")

    local_now = now.astimezone(tz)
    target_date = local_now.date() + timedelta(days=delay.offset_days)
    h, m = (int(p) for p in delay.time_of_day.split(":"))
    local_target = datetime.combine(target_date, time(h, m), tzinfo=tz)
    if local_target <= now:
        local_target += timedelta(days=1)
    return local_target.astimezone(timezone.utc)


async def build_dispatcher(
    session: AsyncSession,
    *,
    location_id: str | None = None,
    runtime: AutomationWorkflowRuntimeService | None = None,
    scheduler: AutomationWorkflowSchedulerService | None = None,
    gate: ComplianceGate | None = None,
    revalidator: RunRevalidator | None = None,
    calendar_jitter_seconds: int = _DEFAULT_CALENDAR_JITTER_SECONDS,
) -> tuple[WorkflowStepDispatcher, str]:
    """Construct a dispatcher wired with the real compliance gate + resolve the
    location's timezone.

    This is the single construction path used by both the API enroll route and the
    Celery dispatch/enroll tasks. Centralizing it prevents the class of bug where a
    caller builds ``WorkflowStepDispatcher(...)`` without a gate (defaulting to
    NoOpComplianceGate) or with a hardcoded ``location_timezone``.

    Returns ``(dispatcher, resolved_location_timezone)``.
    """
    # Lazy import avoids any import cycle between the dispatcher and the gate.
    from src.app.services.automation.compliance_gate_service import ComplianceGateService

    runtime = runtime or AutomationWorkflowRuntimeService(session)
    scheduler = scheduler or AutomationWorkflowSchedulerService(session)
    if gate is None:
        gate = ComplianceGateService(session)

    location_timezone = "UTC"
    if location_id:
        location = await session.get(InstitutionLocation, location_id)
        if location and location.timezone:
            location_timezone = location.timezone

    dispatcher = WorkflowStepDispatcher(
        session,
        runtime,
        scheduler,
        gate=gate,
        revalidator=revalidator,
        calendar_jitter_seconds=calendar_jitter_seconds,
    )
    return dispatcher, location_timezone
