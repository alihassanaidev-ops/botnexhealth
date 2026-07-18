"""Operational campaign overview, run list, and timeline queries.

Read-only projections over existing workflow runtime tables. Responses are
PHI-light: they reference attempts and replies by masked identifiers/statuses,
never raw message bodies or decrypted contact fields.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationTimerStatus,
    AutomationWorkflow,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.models.campaign_response import CampaignResponseEvent, CampaignStaffHandoff
from src.app.models.contact import Contact
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.outbound_voice import WorkflowVoiceAttempt
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.models.usage_event import UsageEvent
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.launch_checklist_service import CampaignLaunchChecklistService

SEND_STEP_TYPES = {
    "send_sms": "sms",
    "send_email": "email",
    "send_voice": "voice",
}
TERMINAL_RUN_STATUSES = {
    AutomationRunStatus.COMPLETED.value,
    AutomationRunStatus.CANCELLED.value,
    AutomationRunStatus.FAILED.value,
    AutomationRunStatus.BLOCKED.value,
}
STUCK_WAITING_AGE = timedelta(minutes=15)


@dataclass(frozen=True)
class RunListFilters:
    status: str | None = None
    outcome: str | None = None
    current_node: str | None = None
    next_due_from: datetime | None = None
    next_due_to: datetime | None = None
    channel: str | None = None
    failure_reason: str | None = None
    contact_search: str | None = None
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True)
class CampaignRunListItem:
    id: str
    workflow_id: str
    workflow_version_id: str
    status: str
    current_step_id: str | None
    current_step_type: str | None
    outcome: str | None
    blocked_reason: str | None
    contact_id: str | None
    contact_name: str | None
    next_due_at: datetime | None
    latest_event_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class CampaignRunList:
    items: list[CampaignRunListItem]
    limit: int
    next_cursor: str | None


@dataclass(frozen=True)
class TimelineItem:
    id: str
    kind: str
    occurred_at: datetime
    title: str
    status: str | None = None
    step_id: str | None = None
    channel: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunTimeline:
    run: CampaignRunListItem
    contact: dict[str, Any]
    items: list[TimelineItem]


@dataclass(frozen=True)
class CampaignOverview:
    workflow_id: str
    workflow_name: str
    workflow_status: str
    trigger_type: str | None
    location_id: str | None
    latest_version: dict[str, Any] | None
    readiness: dict[str, Any]
    channels: list[str]
    run_counts: dict[str, int]
    outcome_counts: dict[str, int]
    response_counts: dict[str, int]
    open_handoff_count: int
    channel_attempts: dict[str, dict[str, Any]]
    recent_outcomes: list[dict[str, Any]]
    generated_at: datetime


@dataclass(frozen=True)
class OperationItem:
    id: str
    run_id: str
    kind: str
    severity: str
    title: str
    status: str | None
    step_id: str | None
    occurred_at: datetime | None
    cancel_eligible: bool
    replay_eligible: bool
    reason: str | None = None


@dataclass(frozen=True)
class CampaignOperations:
    stuck_waiting_runs: list[OperationItem]
    failed_sends: list[OperationItem]
    suppressed_skipped_runs: list[OperationItem]
    open_handoffs: list[OperationItem]
    generated_at: datetime


class CampaignOperationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def overview(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
    ) -> CampaignOverview:
        definition = _definition_or_none(workflow.definition)
        channels = sorted(_channels_used(definition))
        latest_version = _latest_version(workflow)

        checklist = await CampaignLaunchChecklistService(self.session).build(
            workflow,
            institution_id=institution_id,
        )
        readiness = {
            "overall_status": checklist.overall_status,
            "blockers_count": checklist.blockers_count,
            "warnings_count": checklist.warnings_count,
            "unknown_count": checklist.unknown_count,
            "estimate_basis": checklist.estimate_basis,
            "generated_at": checklist.generated_at,
        }

        status_rows = (
            await self.session.execute(
                select(AutomationWorkflowRun.status, func.count())
                .where(
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.workflow_id == str(workflow.id),
                )
                .group_by(AutomationWorkflowRun.status)
            )
        ).all()
        outcome_rows = (
            await self.session.execute(
                select(AutomationWorkflowRun.outcome, func.count())
                .where(
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.workflow_id == str(workflow.id),
                    AutomationWorkflowRun.outcome.is_not(None),
                )
                .group_by(AutomationWorkflowRun.outcome)
            )
        ).all()
        usage_rows = (
            await self.session.execute(
                select(
                    UsageEvent.channel,
                    func.count(),
                    func.coalesce(func.sum(UsageEvent.segments), 0),
                    func.coalesce(func.sum(UsageEvent.dials), 0),
                    func.coalesce(func.sum(UsageEvent.emails), 0),
                    func.coalesce(func.sum(UsageEvent.minutes), 0),
                    func.coalesce(func.sum(UsageEvent.cost_amount), 0),
                )
                .where(
                    UsageEvent.institution_id == institution_id,
                    UsageEvent.workflow_id == str(workflow.id),
                )
                .group_by(UsageEvent.channel)
            )
        ).all()
        response_rows = (
            await self.session.execute(
                select(CampaignResponseEvent.normalized_intent, func.count())
                .where(
                    CampaignResponseEvent.institution_id == institution_id,
                    CampaignResponseEvent.workflow_id == str(workflow.id),
                )
                .group_by(CampaignResponseEvent.normalized_intent)
            )
        ).all()
        open_handoff_count = (
            await self.session.execute(
                select(func.count(CampaignStaffHandoff.id)).where(
                    CampaignStaffHandoff.institution_id == institution_id,
                    CampaignStaffHandoff.workflow_id == str(workflow.id),
                    CampaignStaffHandoff.status.in_(["open", "assigned"]),
                )
            )
        ).scalar_one()
        recent_runs = (
            await self.session.execute(
                select(AutomationWorkflowRun)
                .where(
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.workflow_id == str(workflow.id),
                    AutomationWorkflowRun.outcome.is_not(None),
                )
                .order_by(AutomationWorkflowRun.completed_at.desc().nullslast(), AutomationWorkflowRun.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

        return CampaignOverview(
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            workflow_status=workflow.status,
            trigger_type=workflow.trigger_type,
            location_id=str(workflow.location_id) if workflow.location_id else None,
            latest_version=latest_version,
            readiness=readiness,
            channels=channels,
            run_counts={str(status): int(count) for status, count in status_rows},
            outcome_counts={str(outcome): int(count) for outcome, count in outcome_rows},
            response_counts={str(intent): int(count) for intent, count in response_rows},
            open_handoff_count=int(open_handoff_count or 0),
            channel_attempts={
                str(channel): {
                    "event_count": int(count),
                    "segments": int(segments or 0),
                    "dials": int(dials or 0),
                    "emails": int(emails or 0),
                    "minutes": float(minutes or 0),
                    "cost": float(cost or Decimal("0")),
                }
                for channel, count, segments, dials, emails, minutes, cost in usage_rows
            },
            recent_outcomes=[
                {
                    "run_id": str(run.id),
                    "status": run.status,
                    "outcome": run.outcome,
                    "completed_at": run.completed_at,
                    "created_at": run.created_at,
                }
                for run in recent_runs
            ],
            generated_at=datetime.now(timezone.utc),
        )

    async def list_runs(
        self,
        workflow_id: str,
        *,
        institution_id: str,
        filters: RunListFilters,
    ) -> CampaignRunList:
        stmt = self._filtered_run_query(workflow_id, institution_id, filters)
        stmt = stmt.order_by(
            AutomationWorkflowRun.created_at.desc(),
            AutomationWorkflowRun.id.desc(),
        ).limit(filters.limit + 1)

        runs = list((await self.session.execute(stmt)).scalars().all())
        has_more = len(runs) > filters.limit
        page_runs = runs[: filters.limit]
        next_cursor = _encode_cursor(page_runs[-1]) if has_more and page_runs else None

        return CampaignRunList(
            items=await self._run_items(page_runs),
            limit=filters.limit,
            next_cursor=next_cursor,
        )

    async def timeline(
        self,
        workflow_id: str,
        run_id: str,
        *,
        institution_id: str,
    ) -> RunTimeline | None:
        run = await self._get_run(workflow_id, run_id, institution_id)
        if run is None:
            return None
        run_item = (await self._run_items([run]))[0]
        contact = await self._contact_context(run)

        items: list[TimelineItem] = []
        items.extend(await self._event_items(run))
        items.extend(await self._step_items(run))
        items.extend(await self._timer_items(run))
        items.extend(await self._sms_items(run))
        items.extend(await self._inbound_sms_items(run))
        items.extend(await self._response_items(run))
        items.extend(await self._handoff_items(run))
        items.extend(await self._voice_items(run))
        items.extend(await self._usage_items(run))

        items.sort(key=lambda item: item.occurred_at)
        return RunTimeline(run=run_item, contact=contact, items=items)

    async def operations(
        self,
        workflow_id: str,
        *,
        institution_id: str,
        limit: int = 25,
    ) -> CampaignOperations:
        now = datetime.now(timezone.utc)
        stuck = await self._stuck_waiting(workflow_id, institution_id, now=now, limit=limit)
        failed = await self._failed_sends(workflow_id, institution_id, limit=limit)
        suppressed = await self._suppressed_or_skipped(workflow_id, institution_id, limit=limit)
        handoffs = await self._open_handoffs(workflow_id, institution_id, limit=limit)
        return CampaignOperations(
            stuck_waiting_runs=stuck,
            failed_sends=failed,
            suppressed_skipped_runs=suppressed,
            open_handoffs=handoffs,
            generated_at=now,
        )

    def _filtered_run_query(
        self,
        workflow_id: str,
        institution_id: str,
        filters: RunListFilters,
    ) -> Select[tuple[AutomationWorkflowRun]]:
        stmt = select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.workflow_id == workflow_id,
            AutomationWorkflowRun.institution_id == institution_id,
        )
        if filters.status:
            stmt = stmt.where(AutomationWorkflowRun.status == filters.status)
        if filters.outcome:
            stmt = stmt.where(AutomationWorkflowRun.outcome == filters.outcome)
        if filters.current_node:
            stmt = stmt.where(AutomationWorkflowRun.current_step_id == filters.current_node)
        if filters.failure_reason:
            pattern = f"%{filters.failure_reason}%"
            step_failure = (
                select(AutomationWorkflowStepExecution.id)
                .where(
                    AutomationWorkflowStepExecution.workflow_run_id == AutomationWorkflowRun.id,
                    or_(
                        AutomationWorkflowStepExecution.result_code.ilike(pattern),
                        AutomationWorkflowStepExecution.error_message.ilike(pattern),
                    ),
                )
                .exists()
            )
            stmt = stmt.where(or_(AutomationWorkflowRun.blocked_reason.ilike(pattern), step_failure))
        if filters.channel:
            step_type = {
                "sms": "send_sms",
                "email": "send_email",
                "voice": "send_voice",
            }.get(filters.channel)
            if step_type:
                stmt = stmt.where(
                    select(AutomationWorkflowStepExecution.id)
                    .where(
                        AutomationWorkflowStepExecution.workflow_run_id == AutomationWorkflowRun.id,
                        AutomationWorkflowStepExecution.step_type == step_type,
                    )
                    .exists()
                )
        if filters.next_due_from or filters.next_due_to:
            timer_conditions: list[Any] = [
                AutomationWorkflowTimer.workflow_run_id == AutomationWorkflowRun.id,
                AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
            ]
            if filters.next_due_from:
                timer_conditions.append(AutomationWorkflowTimer.due_at >= filters.next_due_from)
            if filters.next_due_to:
                timer_conditions.append(AutomationWorkflowTimer.due_at <= filters.next_due_to)
            stmt = stmt.where(
                select(AutomationWorkflowTimer.id).where(*timer_conditions).exists()
            )
        if filters.contact_search:
            pattern = f"%{filters.contact_search}%"
            stmt = stmt.join(Contact, Contact.id == AutomationWorkflowRun.contact_id).where(
                or_(
                    Contact.full_name.ilike(pattern),
                    Contact.first_name.ilike(pattern),
                    Contact.last_name.ilike(pattern),
                    Contact.nexhealth_patient_id.ilike(pattern),
                )
            )
        cursor = _decode_cursor(filters.cursor)
        if cursor is not None:
            created_at, run_id = cursor
            stmt = stmt.where(
                or_(
                    AutomationWorkflowRun.created_at < created_at,
                    and_(
                        AutomationWorkflowRun.created_at == created_at,
                        AutomationWorkflowRun.id < run_id,
                    ),
                )
            )
        return stmt

    async def _get_run(
        self, workflow_id: str, run_id: str, institution_id: str
    ) -> AutomationWorkflowRun | None:
        return (
            await self.session.execute(
                select(AutomationWorkflowRun).where(
                    AutomationWorkflowRun.id == run_id,
                    AutomationWorkflowRun.workflow_id == workflow_id,
                    AutomationWorkflowRun.institution_id == institution_id,
                )
            )
        ).scalar_one_or_none()

    async def _run_items(self, runs: list[AutomationWorkflowRun]) -> list[CampaignRunListItem]:
        if not runs:
            return []
        run_ids = [str(run.id) for run in runs]
        timer_rows = (
            await self.session.execute(
                select(AutomationWorkflowTimer.workflow_run_id, func.min(AutomationWorkflowTimer.due_at))
                .where(
                    AutomationWorkflowTimer.workflow_run_id.in_(run_ids),
                    AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
                )
                .group_by(AutomationWorkflowTimer.workflow_run_id)
            )
        ).all()
        event_rows = (
            await self.session.execute(
                select(AutomationWorkflowEvent.workflow_run_id, func.max(AutomationWorkflowEvent.occurred_at))
                .where(AutomationWorkflowEvent.workflow_run_id.in_(run_ids))
                .group_by(AutomationWorkflowEvent.workflow_run_id)
            )
        ).all()
        contacts = (
            await self.session.execute(
                select(Contact).where(
                    Contact.id.in_([str(run.contact_id) for run in runs if run.contact_id])
                )
            )
        ).scalars().all()
        versions = (
            await self.session.execute(
                select(AutomationWorkflowVersion).where(
                    AutomationWorkflowVersion.id.in_(
                        [str(run.workflow_version_id) for run in runs if run.workflow_version_id]
                    )
                )
            )
        ).scalars().all()
        contact_by_id = {str(contact.id): contact for contact in contacts}
        version_by_id = {str(version.id): version for version in versions}
        next_due = {str(run_id): due_at for run_id, due_at in timer_rows}
        latest_event = {str(run_id): occurred_at for run_id, occurred_at in event_rows}

        return [
            CampaignRunListItem(
                id=str(run.id),
                workflow_id=str(run.workflow_id),
                workflow_version_id=str(run.workflow_version_id),
                status=run.status,
                current_step_id=run.current_step_id,
                current_step_type=_current_step_type(
                    run, version_by_id.get(str(run.workflow_version_id))
                ),
                outcome=run.outcome,
                blocked_reason=run.blocked_reason,
                contact_id=str(run.contact_id) if run.contact_id else None,
                contact_name=_contact_name(contact_by_id.get(str(run.contact_id))),
                next_due_at=next_due.get(str(run.id)),
                latest_event_at=latest_event.get(str(run.id)),
                started_at=run.started_at,
                completed_at=run.completed_at,
                created_at=run.created_at,
            )
            for run in runs
        ]

    async def _contact_context(self, run: AutomationWorkflowRun) -> dict[str, Any]:
        if not run.contact_id:
            return {"id": None, "display_name": None, "phone_masked": None}
        contact = await self.session.get(Contact, str(run.contact_id))
        return {
            "id": str(contact.id) if contact else str(run.contact_id),
            "display_name": _contact_name(contact),
            "phone_masked": None,
        }

    async def _event_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        events = (
            await self.session.execute(
                select(AutomationWorkflowEvent)
                .where(AutomationWorkflowEvent.workflow_run_id == str(run.id))
                .order_by(AutomationWorkflowEvent.occurred_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(event.id),
                kind="workflow_event",
                occurred_at=event.occurred_at,
                title=event.event_type.replace(".", " ").title(),
                step_id=event.step_id,
                summary=_event_summary(event),
            )
            for event in events
        ]

    async def _step_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        steps = (
            await self.session.execute(
                select(AutomationWorkflowStepExecution)
                .where(AutomationWorkflowStepExecution.workflow_run_id == str(run.id))
                .order_by(AutomationWorkflowStepExecution.created_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(step.id),
                kind="step_execution",
                occurred_at=step.started_at or step.created_at,
                title=f"{_step_label(step.step_type)} step",
                status=step.status,
                step_id=step.step_id,
                channel=SEND_STEP_TYPES.get(step.step_type),
                summary=_step_summary(step),
                metadata={
                    "attempt_number": step.attempt_number,
                    "max_attempts": step.max_attempts,
                    "result_code": step.result_code,
                    "scheduled_at": step.scheduled_at,
                    "scheduled_local_at": step.scheduled_local_at,
                    "scheduled_timezone": step.scheduled_timezone,
                    "completed_at": step.completed_at,
                },
            )
            for step in steps
        ]

    async def _timer_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        timers = (
            await self.session.execute(
                select(AutomationWorkflowTimer)
                .where(AutomationWorkflowTimer.workflow_run_id == str(run.id))
                .order_by(AutomationWorkflowTimer.created_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(timer.id),
                kind="timer",
                occurred_at=timer.created_at,
                title="Wait scheduled",
                status=timer.status,
                summary=f"Next action due {timer.due_at.isoformat()}",
                metadata={
                    "due_at": timer.due_at,
                    "due_local_at": timer.due_local_at,
                    "timezone": timer.timezone,
                    "fired_at": timer.fired_at,
                    "cancelled_at": timer.cancelled_at,
                },
            )
            for timer in timers
        ]

    async def _sms_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(SmsHistoryLog)
                .where(SmsHistoryLog.workflow_run_id == str(run.id))
                .order_by(SmsHistoryLog.timestamp)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="channel_attempt",
                occurred_at=row.timestamp,
                title="SMS attempt",
                status=row.status,
                channel="sms",
                summary=row.provider_status or row.status,
                metadata={
                    "message_sid": row.message_sid,
                    "to_number_masked": row.to_number_masked,
                    "last_status_at": row.last_status_at,
                },
            )
            for row in rows
        ]

    async def _inbound_sms_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(InboundSmsMessage)
                .where(InboundSmsMessage.workflow_run_id == str(run.id))
                .order_by(InboundSmsMessage.created_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="inbound_reply",
                occurred_at=row.created_at,
                title="Inbound SMS reply",
                status=row.intent,
                channel="sms",
                summary=f"Intent: {row.intent}",
                metadata={
                    "message_sid": row.message_sid,
                    "from_phone_masked": row.from_phone_masked,
                    "to_phone_masked": row.to_phone_masked,
                },
            )
            for row in rows
        ]

    async def _response_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(CampaignResponseEvent)
                .where(CampaignResponseEvent.workflow_run_id == str(run.id))
                .order_by(CampaignResponseEvent.occurred_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="patient_response",
                occurred_at=row.occurred_at,
                title="Patient response",
                status=row.normalized_intent,
                channel=row.channel,
                summary=row.summary or f"Intent: {row.normalized_intent}",
                metadata={
                    "normalized_outcome": row.normalized_outcome,
                    "source": row.source,
                    "source_event_type": row.source_event_type,
                    "source_event_id": row.source_event_id,
                    "confidence": row.confidence,
                },
            )
            for row in rows
        ]

    async def _handoff_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(CampaignStaffHandoff)
                .where(CampaignStaffHandoff.workflow_run_id == str(run.id))
                .order_by(CampaignStaffHandoff.created_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="staff_handoff",
                occurred_at=row.created_at,
                title="Staff handoff",
                status=row.status,
                summary=row.summary or row.reason,
                metadata={
                    "reason": row.reason,
                    "response_event_id": row.response_event_id,
                    "assignee_user_id": row.assignee_user_id,
                    "due_at": row.due_at,
                    "resolved_at": row.resolved_at,
                    "resolution_outcome": row.resolution_outcome,
                },
            )
            for row in rows
        ]

    async def _voice_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(WorkflowVoiceAttempt)
                .where(WorkflowVoiceAttempt.workflow_run_id == str(run.id))
                .order_by(WorkflowVoiceAttempt.created_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="channel_attempt",
                occurred_at=row.created_at,
                title="Voice attempt",
                status=row.dial_outcome or row.status,
                step_id=row.step_id,
                channel="voice",
                summary=_voice_summary(row),
                metadata={
                    "retell_call_id": row.retell_call_id,
                    "from_number_masked": row.from_number_masked,
                    "to_number_masked": row.to_number_masked,
                    "attempt_number": row.attempt_number,
                    "disconnection_reason": row.disconnection_reason,
                },
            )
            for row in rows
        ]

    async def _usage_items(self, run: AutomationWorkflowRun) -> list[TimelineItem]:
        rows = (
            await self.session.execute(
                select(UsageEvent)
                .where(UsageEvent.workflow_run_id == str(run.id))
                .order_by(UsageEvent.occurred_at)
            )
        ).scalars().all()
        return [
            TimelineItem(
                id=str(row.id),
                kind="usage_event",
                occurred_at=row.occurred_at,
                title=f"{str(row.channel).upper()} metered",
                status=row.provider,
                channel=row.channel,
                summary=_usage_summary(row),
                metadata={
                    "direction": row.direction,
                    "provider_message_id": row.provider_message_id,
                    "external_ref": row.external_ref,
                    "currency": row.currency,
                },
            )
            for row in rows
        ]

    async def _stuck_waiting(
        self,
        workflow_id: str,
        institution_id: str,
        *,
        now: datetime,
        limit: int,
    ) -> list[OperationItem]:
        due_before = now - STUCK_WAITING_AGE
        rows = (
            await self.session.execute(
                select(AutomationWorkflowRun, AutomationWorkflowTimer)
                .join(
                    AutomationWorkflowTimer,
                    AutomationWorkflowTimer.workflow_run_id == AutomationWorkflowRun.id,
                )
                .where(
                    AutomationWorkflowRun.workflow_id == workflow_id,
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.status == AutomationRunStatus.WAITING.value,
                    AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
                    AutomationWorkflowTimer.due_at <= due_before,
                )
                .order_by(AutomationWorkflowTimer.due_at)
                .limit(limit)
            )
        ).all()
        return [
            _operation_item(
                run,
                kind="stuck_waiting",
                severity="warning",
                title="Waiting run is overdue",
                status=timer.status,
                step_id=run.current_step_id,
                occurred_at=timer.due_at,
                reason="Timer due time is more than 15 minutes old.",
            )
            for run, timer in rows
        ]

    async def _failed_sends(
        self, workflow_id: str, institution_id: str, *, limit: int
    ) -> list[OperationItem]:
        rows = (
            await self.session.execute(
                select(AutomationWorkflowRun, AutomationWorkflowStepExecution)
                .join(
                    AutomationWorkflowStepExecution,
                    AutomationWorkflowStepExecution.workflow_run_id == AutomationWorkflowRun.id,
                )
                .where(
                    AutomationWorkflowRun.workflow_id == workflow_id,
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowStepExecution.step_type.in_(tuple(SEND_STEP_TYPES)),
                    AutomationWorkflowStepExecution.status == AutomationStepStatus.FAILED.value,
                )
                .order_by(AutomationWorkflowStepExecution.completed_at.desc().nullslast())
                .limit(limit)
            )
        ).all()
        return [
            _operation_item(
                run,
                kind="failed_send",
                severity="critical",
                title=f"{_step_label(step.step_type)} send failed",
                status=step.result_code or step.status,
                step_id=step.step_id,
                occurred_at=step.completed_at or step.created_at,
                reason=step.result_code or "send_failed",
            )
            for run, step in rows
        ]

    async def _suppressed_or_skipped(
        self, workflow_id: str, institution_id: str, *, limit: int
    ) -> list[OperationItem]:
        rows = (
            await self.session.execute(
                select(AutomationWorkflowRun, AutomationWorkflowStepExecution)
                .join(
                    AutomationWorkflowStepExecution,
                    AutomationWorkflowStepExecution.workflow_run_id == AutomationWorkflowRun.id,
                )
                .where(
                    AutomationWorkflowRun.workflow_id == workflow_id,
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowStepExecution.step_type.in_(tuple(SEND_STEP_TYPES)),
                    or_(
                        AutomationWorkflowStepExecution.status.in_(
                            [
                                AutomationStepStatus.SKIPPED.value,
                                AutomationStepStatus.BLOCKED.value,
                            ]
                        ),
                        AutomationWorkflowStepExecution.result_code.ilike("%suppress%"),
                        AutomationWorkflowStepExecution.result_code.ilike("%blocked%"),
                    ),
                )
                .order_by(AutomationWorkflowStepExecution.completed_at.desc().nullslast())
                .limit(limit)
            )
        ).all()
        return [
            _operation_item(
                run,
                kind="suppressed_or_skipped",
                severity="info",
                title=f"{_step_label(step.step_type)} send skipped",
                status=step.result_code or step.status,
                step_id=step.step_id,
                occurred_at=step.completed_at or step.created_at,
                reason=step.result_code or step.status,
            )
            for run, step in rows
        ]

    async def _open_handoffs(
        self, workflow_id: str, institution_id: str, *, limit: int
    ) -> list[OperationItem]:
        rows = (
            await self.session.execute(
                select(CampaignStaffHandoff)
                .where(
                    CampaignStaffHandoff.workflow_id == workflow_id,
                    CampaignStaffHandoff.institution_id == institution_id,
                    CampaignStaffHandoff.status.in_(["open", "assigned"]),
                    CampaignStaffHandoff.workflow_run_id.is_not(None),
                )
                .order_by(CampaignStaffHandoff.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            OperationItem(
                id=str(row.id),
                run_id=str(row.workflow_run_id),
                kind="staff_handoff",
                severity="warning" if row.status == "open" else "info",
                title="Patient response needs staff review",
                status=row.status,
                step_id=None,
                occurred_at=row.created_at,
                cancel_eligible=False,
                replay_eligible=False,
                reason=row.summary or row.reason,
            )
            for row in rows
        ]


def _definition_or_none(raw: dict[str, Any] | None) -> WorkflowDefinition | None:
    if not raw:
        return None
    try:
        return WorkflowDefinition.model_validate(raw)
    except Exception:
        return None


def _channels_used(definition: WorkflowDefinition | None) -> set[str]:
    if definition is None:
        return set()
    channels: set[str] = set()
    for node in definition.nodes:
        if isinstance(node, SendSmsNode):
            channels.add("sms")
        elif isinstance(node, SendEmailNode):
            channels.add("email")
        elif isinstance(node, SendVoiceNode):
            channels.add("voice")
    return channels


def _latest_version(workflow: AutomationWorkflow) -> dict[str, Any] | None:
    versions = sorted(workflow.versions or [], key=lambda v: v.version_number, reverse=True)
    if not versions:
        return None
    latest = versions[0]
    return {
        "id": str(latest.id),
        "version_number": latest.version_number,
        "published_at": latest.published_at,
        "is_current": bool(workflow.current_version_id)
        and str(latest.id) == str(workflow.current_version_id),
        "content_classification": latest.content_classification,
    }


def _current_step_type(
    run: AutomationWorkflowRun, version: AutomationWorkflowVersion | None
) -> str | None:
    definition = _definition_or_none(version.definition if version else None)
    if definition is None or not run.current_step_id:
        return None
    for node in definition.nodes:
        if node.id == run.current_step_id:
            return node.type
    return None


def _contact_name(contact: Contact | None) -> str | None:
    if contact is None:
        return None
    return contact.full_name or " ".join(
        p for p in [contact.first_name, contact.last_name] if p
    ) or None


def _event_summary(event: AutomationWorkflowEvent) -> str | None:
    metadata = event.event_metadata or {}
    if "outcome" in metadata and metadata["outcome"]:
        return f"Outcome: {metadata['outcome']}"
    if "reason" in metadata and metadata["reason"]:
        return f"Reason: {metadata['reason']}"
    if "step_type" in metadata and metadata["step_type"]:
        return f"Step type: {metadata['step_type']}"
    return None


def _step_label(step_type: str) -> str:
    return {
        "send_sms": "SMS",
        "send_email": "Email",
        "send_voice": "Voice",
        "wait": "Wait",
        "condition": "Condition",
        "exit": "Exit",
    }.get(step_type, step_type.replace("_", " ").title())


def _step_summary(step: AutomationWorkflowStepExecution) -> str | None:
    if step.result_code:
        return f"Result: {step.result_code}"
    if step.status == AutomationStepStatus.FAILED.value:
        return "Step failed"
    if step.status == AutomationStepStatus.BLOCKED.value:
        return "Step blocked"
    if step.status == AutomationStepStatus.SKIPPED.value:
        return "Step skipped"
    return None


def _voice_summary(row: WorkflowVoiceAttempt) -> str:
    if row.dial_outcome:
        return f"Outcome: {row.dial_outcome}"
    return f"Status: {row.status}"


def _usage_summary(row: UsageEvent) -> str:
    quantities: list[str] = []
    if row.segments:
        quantities.append(f"{row.segments} segment(s)")
    if row.dials:
        quantities.append(f"{row.dials} dial(s)")
    if row.emails:
        quantities.append(f"{row.emails} email(s)")
    if row.minutes:
        quantities.append(f"{float(row.minutes):.2f} minute(s)")
    if row.cost_amount:
        quantities.append(f"{float(row.cost_amount):.4f} {row.currency}")
    return ", ".join(quantities) or "Usage recorded"


def _operation_item(
    run: AutomationWorkflowRun,
    *,
    kind: str,
    severity: str,
    title: str,
    status: str | None,
    step_id: str | None,
    occurred_at: datetime | None,
    reason: str | None,
) -> OperationItem:
    return OperationItem(
        id=f"{kind}:{run.id}:{step_id or 'run'}:{occurred_at.isoformat() if occurred_at else ''}",
        run_id=str(run.id),
        kind=kind,
        severity=severity,
        title=title,
        status=status,
        step_id=step_id,
        occurred_at=occurred_at,
        cancel_eligible=run.status not in TERMINAL_RUN_STATUSES,
        replay_eligible=False,
        reason=reason or "Replay requires a compliance recheck action before it is enabled.",
    )


def _encode_cursor(run: AutomationWorkflowRun) -> str:
    payload = {"created_at": run.created_at.isoformat(), "id": str(run.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        return created_at, str(payload["id"])
    except Exception:
        return None
