"""Workflow step dispatcher: advances a run through its definition until wait or exit.

SMS sends are live (Plan 04). Voice and email send nodes remain stubbed until
Plans 03 and 05 are implemented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
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
from src.app.services.automation.sms_node_executor import SmsNodeExecutor
from src.app.services.automation.compliance_gate import ComplianceGate, NoOpComplianceGate
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService

logger = logging.getLogger(__name__)

_MAX_STEPS = 50


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
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.scheduler = scheduler
        self.gate: ComplianceGate = gate or NoOpComplianceGate()

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
                gate_result = await self.gate.check(run, node.type)
                if gate_result.action == "block":
                    step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)
                    await self.runtime.fail_step(step, result_code="compliance_blocked")
                    await self.runtime.fail_run(run, reason=gate_result.reason or "compliance_blocked")
                    return DispatchResult(status="failed", steps_advanced=steps_advanced)
                if gate_result.action == "hold":
                    step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)
                    await self.runtime.fail_step(step, result_code="compliance_hold")
                    await self.runtime.complete_run(run, outcome="compliance_hold")
                    return DispatchResult(
                        status="completed", outcome="compliance_hold", steps_advanced=steps_advanced
                    )
                if isinstance(node, SendSmsNode):
                    current_node_id = await SmsNodeExecutor(
                        self.session, self.runtime
                    ).execute(run, node, context)
                else:
                    current_node_id = await self._dispatch_send_stub(run, node)

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
        """Resume a WAITING run after its timer fires, then advance to the next node.

        Finds the waiting step execution, resumes the run, advances the current
        step pointer past the wait node, then calls advance().
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
        wait_node = node_map.get(run.current_step_id or "")
        if not isinstance(wait_node, WaitNode):
            await self.runtime.fail_run(
                run, reason=f"expected wait node at '{run.current_step_id}'"
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
        run.current_step_id = wait_node.next_node_id
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
