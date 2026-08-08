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
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationWorkflowDripState,
    AutomationWorkflowRun,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import (
    AppointmentRelativeDelay,
    CalendarDelay,
    ConditionNode,
    ConditionRule,
    DripNode,
    DurationDelay,
    ExitNode,
    JsonMapperNode,
    LlmNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    UpdateGoTrackerAppointmentNode,
    UpdatePatientStatusNode,
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


class WorkflowGoTrackerWritebackError(RuntimeError):
    """Raised when an explicit GoTracker appointment writeback node fails."""


@dataclass
class DispatchResult:
    status: Literal["waiting", "completed", "failed"]
    timer_id: str | None = None
    outcome: str | None = None
    steps_advanced: int = 0
    patient_status_event_ids: list[str] = field(default_factory=list)


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
        patient_status_event_ids: list[str] = []

        while steps_advanced < _MAX_STEPS:
            node = node_map.get(current_node_id)
            if node is None:
                logger.error(
                    "dispatch: node not found institution=%s run=%s node=%s",
                    run.institution_id, run.id, current_node_id,
                )
                await self.runtime.fail_run(run, reason=f"node '{current_node_id}' not found")
                return DispatchResult(
                    status="failed",
                    steps_advanced=steps_advanced,
                    patient_status_event_ids=patient_status_event_ids,
                )

            steps_advanced += 1

            if isinstance(node, WaitNode):
                due_at = _compute_due_at(node.delay, location_timezone, now, context=context)
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
                    status="waiting",
                    timer_id=timer.id,
                    steps_advanced=steps_advanced,
                    patient_status_event_ids=patient_status_event_ids,
                )

            elif isinstance(node, DripNode):
                due_at, batch_number, batch_position = await self._allocate_drip_slot(
                    run, node, now
                )
                step = await self.runtime.begin_step(
                    run,
                    step_id=node.id,
                    step_type="drip",
                    scheduled_at=due_at,
                    scheduled_timezone=location_timezone,
                )
                metadata = {
                    "batch_number": batch_number,
                    "batch_position": batch_position,
                    "batch_size": node.batch_size,
                    "interval_seconds": node.interval_seconds,
                    "release_at": due_at.isoformat(),
                }
                if due_at <= now:
                    await self.runtime.complete_step(
                        step,
                        result_code="drip_released",
                        result_metadata=metadata,
                    )
                    current_node_id = node.next_node_id
                    continue

                step.result_metadata = metadata
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
                    status="waiting",
                    timer_id=timer.id,
                    steps_advanced=steps_advanced,
                    patient_status_event_ids=patient_status_event_ids,
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
                        status="completed",
                        outcome=skip_outcome,
                        steps_advanced=steps_advanced,
                        patient_status_event_ids=patient_status_event_ids,
                    )

                content_class = (
                    definition.compliance.content_class if definition.compliance else None
                )
                gate_result = await self.gate.check(run, node.type, content_class=content_class)
                if gate_result.action == "block":
                    step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)
                    await self.runtime.fail_step(step, result_code="compliance_blocked")
                    await self.runtime.fail_run(run, reason=gate_result.reason or "compliance_blocked")
                    return DispatchResult(
                        status="failed",
                        steps_advanced=steps_advanced,
                        patient_status_event_ids=patient_status_event_ids,
                    )
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
                        status="waiting",
                        timer_id=timer.id,
                        steps_advanced=steps_advanced,
                        patient_status_event_ids=patient_status_event_ids,
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
                            status="waiting",
                            timer_id=timer.id,
                            steps_advanced=steps_advanced,
                            patient_status_event_ids=patient_status_event_ids,
                        )
                    current_node_id = dispatch_result

            elif isinstance(node, UpdatePatientStatusNode):
                current_node_id, event_id = await self._record_patient_status(
                    run, node, context
                )
                patient_status_event_ids.append(event_id)

            elif isinstance(node, UpdateGoTrackerAppointmentNode):
                try:
                    current_node_id = await self._update_gotracker_appointment(
                        run, node, context, location_timezone=location_timezone
                    )
                except WorkflowGoTrackerWritebackError:
                    return DispatchResult(
                        status="failed",
                        steps_advanced=steps_advanced,
                        patient_status_event_ids=patient_status_event_ids,
                    )

            elif isinstance(node, JsonMapperNode):
                step = await self.runtime.begin_step(run, step_id=node.id, step_type="json_mapper")
                mapped: dict[str, object] = {}
                for mapping in node.mappings:
                    value = _context_value(context, mapping.source_path)
                    if value is None:
                        value = mapping.default_value
                    _assign_context_value(context, mapping.target_field, value)
                    mapped[mapping.target_field] = _metadata_value(value)
                await self.runtime.complete_step(
                    step,
                    result_code="mapped",
                    result_metadata={"mapped_fields": mapped},
                )
                current_node_id = node.next_node_id

            elif isinstance(node, LlmNode):
                from src.app.services.automation.llm_node_executor import (
                    WorkflowLlmError,
                    execute_llm_node,
                )

                step = await self.runtime.begin_step(run, step_id=node.id, step_type="llm")
                try:
                    llm_result = await execute_llm_node(node, context)
                except WorkflowLlmError as exc:
                    await self.runtime.fail_step(
                        step,
                        error_message=str(exc),
                        result_code="llm_failed",
                    )
                    await self.runtime.fail_run(run, reason="llm_failed")
                    return DispatchResult(
                        status="failed",
                        steps_advanced=steps_advanced,
                        patient_status_event_ids=patient_status_event_ids,
                    )
                await self.runtime.complete_step(
                    step,
                    result_code="classified",
                    result_metadata=llm_result.metadata,
                )
                current_node_id = node.next_node_id

            elif isinstance(node, ConditionNode):
                branch = _evaluate_condition(node, context)
                step = await self.runtime.begin_step(run, step_id=node.id, step_type="condition")
                await self.runtime.complete_step(
                    step,
                    result_code=f"branch_{'true' if branch else 'false'}",
                    result_metadata={
                        "branch": "true" if branch else "false",
                        "next_node_id": node.true_next_node_id
                        if branch
                        else node.false_next_node_id,
                    },
                )
                current_node_id = (
                    node.true_next_node_id if branch else node.false_next_node_id
                )

            elif isinstance(node, ExitNode):
                step = await self.runtime.begin_step(run, step_id=node.id, step_type="exit")
                await self.runtime.complete_step(step, result_code=node.outcome or "exit")
                await self.runtime.complete_run(run, outcome=node.outcome)
                return DispatchResult(
                    status="completed",
                    outcome=node.outcome,
                    steps_advanced=steps_advanced,
                    patient_status_event_ids=patient_status_event_ids,
                )

        logger.error(
            "dispatch: max step limit institution=%s run=%s", run.institution_id, run.id
        )
        await self.runtime.fail_run(run, reason="max step limit exceeded")
        return DispatchResult(
            status="failed",
            steps_advanced=steps_advanced,
            patient_status_event_ids=patient_status_event_ids,
        )

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
        is_drip = isinstance(current_node, DripNode)
        is_held_send = isinstance(current_node, (SendSmsNode, SendVoiceNode, SendEmailNode))
        if not (is_wait or is_drip or is_held_send):
            await self.runtime.fail_run(
                run, reason=f"expected wait, drip, or held send node at '{run.current_step_id}'"
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
        if is_drip:
            waiting_step.result_code = "drip_released"
        is_parked_voice = is_held_send and waiting_step.result_code == _CALL_PLACED_AWAITING
        if is_wait or is_drip or is_parked_voice:
            # WaitNode/DripNode: move past the gate. Parked voice: the call already
            # went out, so advance PAST the send node (never re-dial) into whatever
            # follows — typically a ConditionNode that branches on `call_outcome`.
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

    async def _allocate_drip_slot(
        self,
        run: AutomationWorkflowRun,
        node: DripNode,
        now: datetime,
    ) -> tuple[datetime, int, int]:
        """Reserve this run's release slot for a Drip action.

        The state row is scoped to the immutable workflow version and node id, so
        publishing edited drip settings naturally gives new runs a fresh cursor
        while already queued runs keep their original timers.
        """
        result = await self.session.execute(
            select(AutomationWorkflowDripState)
            .where(
                AutomationWorkflowDripState.workflow_version_id
                == run.workflow_version_id,
                AutomationWorkflowDripState.step_id == node.id,
            )
            .with_for_update()
        )
        state = result.scalar_one_or_none()
        if state is None:
            await self.session.execute(
                pg_insert(AutomationWorkflowDripState)
                .values(
                    institution_id=run.institution_id,
                    location_id=run.location_id,
                    workflow_id=run.workflow_id,
                    workflow_version_id=run.workflow_version_id,
                    step_id=node.id,
                    batch_size=node.batch_size,
                    interval_seconds=node.interval_seconds,
                    current_batch_number=0,
                    current_batch_count=0,
                    next_due_at=now,
                )
                .on_conflict_do_nothing(
                    constraint="uq_automation_drip_state_version_step"
                )
            )
            result = await self.session.execute(
                select(AutomationWorkflowDripState)
                .where(
                    AutomationWorkflowDripState.workflow_version_id
                    == run.workflow_version_id,
                    AutomationWorkflowDripState.step_id == node.id,
                )
                .with_for_update()
            )
            state = result.scalar_one()

        if state.next_due_at is None:
            state.next_due_at = now

        if state.current_batch_count >= node.batch_size:
            next_due_at = state.next_due_at + timedelta(seconds=node.interval_seconds)
            if next_due_at < now:
                next_due_at = now
            state.next_due_at = next_due_at
            state.current_batch_number += 1
            state.current_batch_count = 0

        due_at = state.next_due_at
        batch_number = state.current_batch_number + 1
        batch_position = state.current_batch_count + 1
        state.batch_size = node.batch_size
        state.interval_seconds = node.interval_seconds
        state.current_batch_count += 1
        await self.session.flush()
        return due_at, batch_number, batch_position

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

    async def _record_patient_status(
        self,
        run: AutomationWorkflowRun,
        node: UpdatePatientStatusNode,
        context: dict,
    ) -> tuple[str, str]:
        """Record a local campaign/patient status event for workflow branching.

        This intentionally does not write back to PMS. It gives the workflow engine
        a durable patient-journey/status trail that can later drive dashboards,
        follow-up workflows, or staff queues.
        """
        from src.app.models.patient_workflow_status import PatientWorkflowStatusEvent
        from src.app.services.automation.template_renderer import render_sms_body

        step = await self.runtime.begin_step(
            run, step_id=node.id, step_type=node.type
        )
        note = None
        if node.note_template:
            note = render_sms_body(node.note_template, None, None, context)

        event = PatientWorkflowStatusEvent(
            institution_id=run.institution_id,
            location_id=run.location_id,
            contact_id=run.contact_id,
            workflow_id=run.workflow_id,
            workflow_version_id=run.workflow_version_id,
            workflow_run_id=run.id,
            step_id=node.id,
            trigger_ref_type=run.trigger_ref_type,
            trigger_ref_id=run.trigger_ref_id,
            status=node.status,
            note=note,
        )
        self.session.add(event)
        await self._apply_status_side_effects(run, node)
        context["patient_workflow_status"] = node.status
        context["patient_status"] = node.status
        await self.runtime.complete_step(
            step,
            result_code="status_updated",
            result_metadata={"status": node.status},
        )
        await self.session.flush()
        return node.next_node_id, str(event.id)

    async def _update_gotracker_appointment(
        self,
        run: AutomationWorkflowRun,
        node: UpdateGoTrackerAppointmentNode,
        context: dict,
        *,
        location_timezone: str = "UTC",
    ) -> str:
        from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
        from src.app.models.institution import Institution
        from src.app.pms.factory import get_adapter_for_institution_location
        from src.app.services.audit import log_audit
        from src.app.services.automation.nexhealth_projection_service import (
            NexHealthProjectionService,
        )

        step = await self.runtime.begin_step(
            run, step_id=node.id, step_type=node.type
        )

        async def fail(reason: str, metadata: dict | None = None) -> None:
            await self.runtime.fail_step(
                step,
                error_message=reason,
                result_code="gotracker_writeback_failed",
                result_metadata=metadata or {},
            )
            await self.runtime.fail_run(run, reason="gotracker_writeback_failed")

        if run.trigger_ref_type != "appointment" or not run.trigger_ref_id:
            await fail("GoTracker writeback requires an appointment-triggered run")
            raise WorkflowGoTrackerWritebackError("missing appointment reference")
        if not run.location_id:
            await fail("GoTracker writeback requires a location-scoped run")
            raise WorkflowGoTrackerWritebackError("missing location")

        institution = await self.session.get(Institution, run.institution_id)
        location = await self.session.get(InstitutionLocation, run.location_id)
        if institution is None or location is None:
            await fail("GoTracker writeback could not resolve institution/location")
            raise WorkflowGoTrackerWritebackError("missing institution/location")

        adapter = None
        operations: list[dict[str, object]] = []
        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            if getattr(adapter, "source", None) != "gotracker":
                await fail("GoTracker writeback can only run for GoTracker locations")
                raise WorkflowGoTrackerWritebackError("non-gotracker adapter")

            status_payload = _gotracker_status_payload(node)
            if status_payload:
                if node.status_id is not None:
                    result = await adapter.set_appointment_status_id(
                        str(run.trigger_ref_id),
                        status_id=node.status_id,
                        confirmed=node.confirmed,
                        preconfirmed=node.preconfirmed,
                    )
                else:
                    result = await adapter.set_appointment_confirmation(
                        str(run.trigger_ref_id),
                        confirmed=node.confirmed,
                        preconfirmed=node.preconfirmed,
                    )
                operations.append({"endpoint": "status", "payload": status_payload})
                await log_audit(
                    actor=AuditActor.SYSTEM,
                    action=_gotracker_status_audit_action(node),
                    target_resource=f"appointment:{run.trigger_ref_id}",
                    outcome=(
                        AuditOutcome.SUCCESS
                        if result.success
                        else AuditOutcome.FAILURE_EXTERNAL_API
                    ),
                    metadata={
                        "source": "workflow_gotracker_writeback",
                        "workflow_run_id": str(run.id),
                        "step_id": node.id,
                        "payload": status_payload,
                        "pms_status": result.status,
                        "error": result.error,
                    },
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id),
                )
                if not result.success:
                    await fail(
                        result.error or "GoTracker status update failed",
                        {"operations": operations},
                    )
                    raise WorkflowGoTrackerWritebackError("status update failed")
                await NexHealthProjectionService(self.session).record_gotracker_writeback(
                    institution_id=str(run.institution_id),
                    appointment_id=str(run.trigger_ref_id),
                    location_id=str(run.location_id),
                    status_id=node.status_id,
                    confirmed=node.confirmed,
                    preconfirmed=node.preconfirmed,
                )
                from src.app.tasks.automation_workflow import (
                    trigger_appointment_state_workflows,
                )

                trigger_appointment_state_workflows.delay(
                    institution_id=str(run.institution_id),
                    appointment_id=str(run.trigger_ref_id),
                    contact_id=str(run.contact_id) if run.contact_id else None,
                    location_id=str(run.location_id),
                    status_id=node.status_id,
                    confirmed=node.confirmed,
                    preconfirmed=node.preconfirmed,
                    trigger_metadata=context,
                )

            update_payload = {
                "start_time": _render_gotracker_update_value(node.start_time, context),
                "end_time": _render_gotracker_update_value(node.end_time, context),
                "duration_min": node.duration_min,
                "provider_id": _render_gotracker_update_value(node.provider_id, context),
                "operatory_id": _render_gotracker_update_value(node.operatory_id, context),
                "patient_id": _render_gotracker_update_value(node.patient_id, context),
                "reason": _render_gotracker_update_value(node.reason, context),
            }
            update_payload = {
                key: value for key, value in update_payload.items() if value is not None
            }
            normalized_start_time: str | None = None
            if isinstance(update_payload.get("start_time"), str):
                parsed_start = _parse_context_datetime(
                    update_payload["start_time"], location_timezone
                )
                if parsed_start is None:
                    await fail(
                        "GoTracker appointment start_time is not a valid ISO datetime",
                        {"operations": operations},
                    )
                    raise WorkflowGoTrackerWritebackError("invalid appointment start_time")
                normalized_start_time = parsed_start.isoformat()
            if update_payload:
                result = await adapter.update_appointment(
                    str(run.trigger_ref_id),
                    start_time=update_payload.get("start_time"),
                    end_time=update_payload.get("end_time"),
                    duration_min=update_payload.get("duration_min"),
                    provider_id=update_payload.get("provider_id"),
                    operatory_id=update_payload.get("operatory_id"),
                    patient_id=update_payload.get("patient_id"),
                    reason=update_payload.get("reason"),
                )
                operations.append({"endpoint": "appointment", "payload": update_payload})
                await log_audit(
                    actor=AuditActor.SYSTEM,
                    action=AuditAction.RESCHEDULE_APPOINTMENT,
                    target_resource=f"appointment:{run.trigger_ref_id}",
                    outcome=(
                        AuditOutcome.SUCCESS
                        if result.success
                        else AuditOutcome.FAILURE_EXTERNAL_API
                    ),
                    metadata={
                        "source": "workflow_gotracker_writeback",
                        "workflow_run_id": str(run.id),
                        "step_id": node.id,
                        "payload": update_payload,
                        "pms_status": result.status,
                        "error": result.error,
                    },
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id),
                )
                if not result.success:
                    await fail(
                        result.error or "GoTracker appointment update failed",
                        {"operations": operations},
                    )
                    raise WorkflowGoTrackerWritebackError("appointment update failed")
                await NexHealthProjectionService(self.session).record_gotracker_writeback(
                    institution_id=str(run.institution_id),
                    appointment_id=str(run.trigger_ref_id),
                    location_id=str(run.location_id),
                    start_time=normalized_start_time,
                    provider_id=update_payload.get("provider_id")
                    if isinstance(update_payload.get("provider_id"), str)
                    else None,
                )
                if normalized_start_time is not None:
                    # The projection write above can make the returning GoTracker
                    # webhook appear unchanged. Explicitly emit the time-aware,
                    # idempotent appointment trigger so a real reschedule gets one
                    # fresh confirmation cycle for its new start time.
                    from src.app.tasks.automation_workflow import (
                        trigger_appointment_workflows,
                    )

                    reschedule_context = {
                        **context,
                        "appointment_at": normalized_start_time,
                        "appointment_datetime": normalized_start_time,
                        "source": "workflow_gotracker_reschedule",
                        "origin_workflow_run_id": str(run.id),
                    }
                    trigger_appointment_workflows.delay(
                        institution_id=str(run.institution_id),
                        appointment_id=str(run.trigger_ref_id),
                        appointment_at_iso=normalized_start_time,
                        contact_id=str(run.contact_id) if run.contact_id else None,
                        location_id=str(run.location_id),
                        trigger_metadata=reschedule_context,
                    )

            await self.runtime.complete_step(
                step,
                result_code="gotracker_updated",
                result_metadata={"operations": operations},
            )
            return node.next_node_id
        finally:
            if adapter is not None:
                await adapter.close()

    async def _apply_status_side_effects(
        self,
        run: AutomationWorkflowRun,
        node: UpdatePatientStatusNode,
    ) -> None:
        """Apply durable side effects implied by local workflow statuses."""
        if node.status != "do_not_call_requested" or not run.contact_id:
            return

        from src.app.models.contact import Contact
        from src.app.models.sms_consent import ConsentSource, DncScope
        from src.app.services.sms_compliance import SmsComplianceService

        contact = await self.session.get(Contact, run.contact_id)
        phone = contact.phone if contact else None
        if not phone:
            logger.warning(
                "status side effect: do_not_call_requested has no phone run=%s contact=%s",
                run.id,
                run.contact_id,
            )
            return

        await SmsComplianceService(self.session).set_do_not_contact(
            institution_id=run.institution_id,
            phone=phone,
            scope=DncScope.LOCATION,
            location_id=run.location_id,
            contact_id=run.contact_id,
            source=ConsentSource.SYSTEM,
            reason="workflow_do_not_call_requested",
        )


def _evaluate_condition(node: ConditionNode, context: dict) -> bool:
    results = [_evaluate_rule(rule, context) for rule in node.rules]
    return all(results) if node.logic == "AND" else any(results)


def _evaluate_rule(rule: ConditionRule, context: dict) -> bool:
    value = _context_value(context, rule.field)
    if rule.op == "eq":
        return value == rule.value
    if rule.op == "neq":
        return value != rule.value
    if rule.op == "in":
        return value in (rule.value or [])
    if rule.op == "in_case_insensitive":
        if value is None or not isinstance(rule.value, list):
            return False
        normalized = str(value).strip().casefold()
        return any(normalized == str(item).strip().casefold() for item in rule.value)
    if rule.op == "not_in":
        return value not in (rule.value or [])
    if rule.op == "is_null":
        return value is None
    if rule.op == "is_not_null":
        return value is not None
    if rule.op == "contains":
        return _contains(value, rule.value)
    if rule.op == "not_contains":
        return not _contains(value, rule.value)
    return False


def _context_value(context: dict, path: str) -> object:
    if path in context:
        return context.get(path)

    current: object = context
    for part in _path_parts(path):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _assign_context_value(context: dict, path: str, value: object) -> None:
    parts = _path_parts(path)
    if not parts:
        return
    if len(parts) == 1:
        context[parts[0]] = value
        return

    current = context
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _path_parts(path: str) -> list[str]:
    return [
        part.strip()
        for part in path.replace("[", ".").replace("]", "").split(".")
        if part.strip()
    ]


def _classify_with_label_rules(node: LlmNode, source_value: object) -> str:
    source_text = _value_to_text(source_value).casefold()
    for rule in node.label_rules:
        if any(keyword.casefold() in source_text for keyword in rule.keywords):
            return rule.label

    for label in node.labels:
        if label.casefold() in source_text:
            return label

    return node.fallback_label or "unknown"


def _value_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_value_to_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_value_to_text(item) for item in value.values())
    return str(value)


def _metadata_value(value: object) -> object:
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_metadata_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _metadata_value(item) for key, item in value.items()}
    return str(value)


def _render_gotracker_update_value(value: str | None, context: dict) -> str | None:
    if value is None:
        return None
    from src.app.services.automation.template_renderer import render_sms_body

    rendered = render_sms_body(value, None, None, context).strip()
    return rendered or None


def _gotracker_status_payload(node: UpdateGoTrackerAppointmentNode) -> dict[str, object]:
    payload: dict[str, object] = {}
    if node.status_id is not None:
        payload["status_id"] = node.status_id
    if node.confirmed is not None:
        payload["confirmed"] = node.confirmed
    if node.preconfirmed is not None:
        payload["preconfirmed"] = node.preconfirmed
    return payload


def _gotracker_status_audit_action(node: UpdateGoTrackerAppointmentNode):
    from src.app.models.audit_log import AuditAction

    if node.status_id == 3:
        return AuditAction.CANCEL_APPOINTMENT
    if node.confirmed is True:
        return AuditAction.CONFIRM_APPOINTMENT
    return AuditAction.UPDATE_APPOINTMENT


def _contains(value: object, expected: object) -> bool:
    if value is None or expected is None:
        return False
    value_text = str(value).casefold()
    if isinstance(expected, list):
        return any(str(item).casefold() in value_text for item in expected)
    return str(expected).casefold() in value_text


def _compute_due_at(
    delay: DurationDelay | CalendarDelay | AppointmentRelativeDelay,
    location_timezone: str,
    now: datetime,
    context: dict | None = None,
) -> datetime:
    if isinstance(delay, DurationDelay):
        return now + timedelta(seconds=delay.duration_seconds)
    if isinstance(delay, AppointmentRelativeDelay):
        raw_anchor = (context or {}).get(delay.anchor_field)
        anchor = _parse_context_datetime(raw_anchor, location_timezone)
        if anchor is None:
            logger.warning(
                "appointment-relative wait missing/invalid anchor '%s'",
                delay.anchor_field,
            )
            return now
        target = anchor + timedelta(seconds=delay.offset_seconds)
        return target if target > now else now

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


def _parse_context_datetime(value: object, location_timezone: str = "UTC") -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(location_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning("unknown timezone '%s', falling back to UTC", location_timezone)
            tz = ZoneInfo("UTC")
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


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
