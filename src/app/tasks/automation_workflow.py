"""Celery tasks for the automation workflow engine scheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.app.database import get_system_db_session, init_database, is_database_initialized
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationTimerStatus,
    AutomationWorkflowRun,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.appointment_trigger_service import (
    AppointmentTriggerService,
    compute_enrollment_eta,
    make_appointment_idempotency_key,
)
from src.app.services.automation.callback_trigger_service import (
    CallbackTriggerService,
    compute_callback_eta,
    make_callback_idempotency_key,
)
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.compliance_gate_service import ComplianceGateService
from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher
from src.app.worker import celery_app

logger = logging.getLogger(__name__)

_CLAIM_BATCH = 50
_CLAIM_TTL_SECONDS = 120

# Run statuses that can be advanced by a fired timer.
_ADVANCEABLE_STATUSES = frozenset({
    AutomationRunStatus.WAITING.value,
    AutomationRunStatus.RUNNING.value,
})


def _ensure_db() -> None:
    from src.app.config import settings
    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


# ---------------------------------------------------------------------------
# Poller task — runs on Celery beat every 30 s
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.poll_workflow_timers",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def poll_workflow_timers(self) -> dict:
    """Claim due workflow timers and enqueue a dispatch task per timer."""
    _ensure_db()
    try:
        return asyncio.run(_claim_and_enqueue_async())
    except Exception as exc:
        logger.exception("poll_workflow_timers failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


async def _claim_and_enqueue_async() -> dict:
    now = datetime.now(tz=timezone.utc)
    # NOTE: The Celery DB role must have cross-institution visibility on
    # automation_workflow_timers (BYPASSRLS or a scheduler-specific policy).
    async with get_system_db_session(
        "celery", external_id="workflow_scheduler_poll"
    ) as session:
        svc = AutomationWorkflowSchedulerService(session)
        timers = await svc.claim_due_timers(
            now=now, limit=_CLAIM_BATCH, claim_ttl_seconds=_CLAIM_TTL_SECONDS
        )
        await session.commit()

    claimed = [
        (t.id, t.institution_id, t.location_id, t.workflow_run_id) for t in timers
    ]
    logger.info("poll_workflow_timers: claimed %d timer(s)", len(claimed))

    for timer_id, institution_id, location_id, run_id in claimed:
        dispatch_workflow_timer.apply_async(
            kwargs={
                "timer_id": timer_id,
                "institution_id": institution_id,
                "location_id": location_id,
                "run_id": run_id,
            },
            queue="workflow",
        )

    return {"claimed": len(claimed)}


# ---------------------------------------------------------------------------
# Per-timer dispatch task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.dispatch_workflow_timer",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def dispatch_workflow_timer(
    self,
    *,
    timer_id: str,
    institution_id: str,
    location_id: str | None,
    run_id: str,
) -> dict:
    """Load a claimed timer and advance its run through the workflow definition."""
    _ensure_db()
    try:
        return asyncio.run(
            _dispatch_timer_async(
                timer_id=timer_id,
                institution_id=institution_id,
                location_id=location_id,
                run_id=run_id,
            )
        )
    except Exception as exc:
        logger.exception(
            "dispatch_workflow_timer failed: timer=%s run=%s: %s", timer_id, run_id, exc
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _dispatch_timer_async(
    *,
    timer_id: str,
    institution_id: str,
    location_id: str | None,
    run_id: str,
) -> dict:
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=timer_id,
    ) as session:
        # Load and validate timer is still claimed.
        timer = await session.get(AutomationWorkflowTimer, timer_id)
        if timer is None or timer.status != AutomationTimerStatus.CLAIMED.value:
            logger.warning("dispatch: timer %s not found or not claimed", timer_id)
            return {"skipped": True, "reason": "timer not claimed"}

        # Load run — skip if already terminal.
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None or run.status not in _ADVANCEABLE_STATUSES:
            svc = AutomationWorkflowSchedulerService(session)
            await svc.fire_timer(timer)
            await session.commit()
            return {"skipped": True, "reason": "run not advanceable"}

        # Load workflow version and parse definition.
        version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            logger.error("dispatch: version %s not found for run %s", run.workflow_version_id, run_id)
            return {"skipped": True, "reason": "version not found"}

        definition = WorkflowDefinition.model_validate(version.definition)

        # Resolve location timezone.
        location_timezone = "UTC"
        if run.location_id:
            location = await session.get(InstitutionLocation, run.location_id)
            if location and location.timezone:
                location_timezone = location.timezone

        # Build services and fire timer before dispatch.
        runtime = AutomationWorkflowRuntimeService(session)
        scheduler = AutomationWorkflowSchedulerService(session)
        dispatcher = WorkflowStepDispatcher(session, runtime, scheduler, gate=ComplianceGateService(session))

        await scheduler.fire_timer(timer)

        result = await dispatcher.resume_after_timer(
            run,
            definition,
            context=run.trigger_metadata or {},
            location_timezone=location_timezone,
        )

        await session.commit()

    logger.info(
        "dispatch: timer=%s run=%s status=%s steps=%d",
        timer_id, run_id, result.status, result.steps_advanced,
    )
    return {
        "timer_id": timer_id,
        "run_id": run_id,
        "dispatch_status": result.status,
        "steps_advanced": result.steps_advanced,
        "outcome": result.outcome,
    }


def _retry_countdown(retries: int) -> int:
    return min(300, 2 ** max(retries, 0))


# ---------------------------------------------------------------------------
# Enrollment + start + advance (shared by appointment trigger and bulk enroll)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.enroll_and_start_workflow_run",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def enroll_and_start_workflow_run(
    self,
    *,
    institution_id: str,
    workflow_id: str,
    workflow_version_id: str,
    contact_id: str | None,
    location_id: str | None,
    trigger_type: str | None,
    trigger_ref_type: str | None,
    trigger_ref_id: str | None,
    idempotency_key: str,
    trigger_metadata: dict,
) -> dict:
    """Enroll a contact in a workflow, start the run, and advance through the definition.

    Designed to be scheduled with an ETA for appointment-offset triggers, or
    called immediately for manual/bulk/recall triggers.
    """
    _ensure_db()
    try:
        return asyncio.run(
            _enroll_and_start_async(
                institution_id=institution_id,
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                contact_id=contact_id,
                location_id=location_id,
                trigger_type=trigger_type,
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                idempotency_key=idempotency_key,
                trigger_metadata=trigger_metadata,
            )
        )
    except Exception as exc:
        logger.exception(
            "enroll_and_start_workflow_run failed: workflow=%s contact=%s: %s",
            workflow_id, contact_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _enroll_and_start_async(
    *,
    institution_id: str,
    workflow_id: str,
    workflow_version_id: str,
    contact_id: str | None,
    location_id: str | None,
    trigger_type: str | None,
    trigger_ref_type: str | None,
    trigger_ref_id: str | None,
    idempotency_key: str,
    trigger_metadata: dict,
) -> dict:
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=idempotency_key,
    ) as session:
        enroll_svc = AutomationWorkflowEnrollmentService(session)
        run, created = await enroll_svc.enroll(
            institution_id=institution_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            contact_id=contact_id,
            location_id=location_id,
            trigger_type=trigger_type,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            trigger_metadata=trigger_metadata,
            idempotency_key=idempotency_key,
        )

        if not created:
            logger.info(
                "enroll_and_start: duplicate idempotency_key=%s — skipping", idempotency_key
            )
            await session.commit()
            return {"run_id": str(run.id), "created": False}

        version = await session.get(AutomationWorkflowVersion, workflow_version_id)
        if version is None:
            logger.error(
                "enroll_and_start: version %s not found for workflow %s",
                workflow_version_id, workflow_id,
            )
            await session.commit()
            return {"run_id": str(run.id), "created": True, "skipped": True}

        location_timezone = "UTC"
        if location_id:
            location = await session.get(InstitutionLocation, location_id)
            if location and location.timezone:
                location_timezone = location.timezone

        definition = WorkflowDefinition.model_validate(version.definition)
        runtime = AutomationWorkflowRuntimeService(session)
        scheduler = AutomationWorkflowSchedulerService(session)
        dispatcher = WorkflowStepDispatcher(session, runtime, scheduler, gate=ComplianceGateService(session))

        await runtime.start_run(run)
        result = await dispatcher.advance(
            run, definition, context=trigger_metadata, location_timezone=location_timezone
        )
        await session.commit()

    logger.info(
        "enroll_and_start: workflow=%s run=%s status=%s steps=%d",
        workflow_id, run.id, result.status, result.steps_advanced,
    )
    return {
        "run_id": str(run.id),
        "created": True,
        "dispatch_status": result.status,
        "steps_advanced": result.steps_advanced,
        "outcome": result.outcome,
    }


# ---------------------------------------------------------------------------
# Appointment trigger — Slice 10 (Plan 09)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.trigger_appointment_workflows",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def trigger_appointment_workflows(
    self,
    *,
    institution_id: str,
    appointment_id: str,
    appointment_at_iso: str,
    contact_id: str | None = None,
    location_id: str | None = None,
    trigger_metadata: dict | None = None,
) -> dict:
    """Find matching AppointmentOffsetTrigger workflows and schedule enrollments.

    Called from a NexHealth webhook handler or appointment sync job whenever
    an appointment is created or updated. Each matching workflow gets an
    enroll_and_start_workflow_run task scheduled at appointment_at + offset_hours.
    """
    _ensure_db()
    try:
        return asyncio.run(
            _trigger_appointment_async(
                institution_id=institution_id,
                appointment_id=appointment_id,
                appointment_at_iso=appointment_at_iso,
                contact_id=contact_id,
                location_id=location_id,
                trigger_metadata=trigger_metadata or {},
            )
        )
    except Exception as exc:
        logger.exception(
            "trigger_appointment_workflows failed: institution=%s appt=%s: %s",
            institution_id, appointment_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _trigger_appointment_async(
    *,
    institution_id: str,
    appointment_id: str,
    appointment_at_iso: str,
    contact_id: str | None,
    location_id: str | None,
    trigger_metadata: dict,
) -> dict:
    from datetime import datetime, timezone

    appointment_at = datetime.fromisoformat(appointment_at_iso)
    if appointment_at.tzinfo is None:
        appointment_at = appointment_at.replace(tzinfo=timezone.utc)

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"appt_trigger:{appointment_id}",
    ) as session:
        svc = AppointmentTriggerService(session)
        workflows = await svc.find_active_appointment_workflows(institution_id)

    scheduled = 0
    skipped = 0
    for wf in workflows:
        if not wf.current_version_id:
            continue
        eta = compute_enrollment_eta(wf, appointment_at)
        if eta is None:
            skipped += 1
            logger.info(
                "trigger_appointment: skipping past-window appt=%s workflow=%s",
                appointment_id, wf.id,
            )
            continue

        idempotency_key = make_appointment_idempotency_key(
            str(wf.current_version_id), appointment_id
        )
        enroll_and_start_workflow_run.apply_async(
            kwargs={
                "institution_id": institution_id,
                "workflow_id": str(wf.id),
                "workflow_version_id": str(wf.current_version_id),
                "contact_id": contact_id,
                "location_id": location_id,
                "trigger_type": "appointment_offset",
                "trigger_ref_type": "appointment",
                "trigger_ref_id": appointment_id,
                "idempotency_key": idempotency_key,
                "trigger_metadata": {
                    **trigger_metadata,
                    "appointment_id": appointment_id,
                    "appointment_at": appointment_at_iso,
                },
            },
            eta=eta,
            queue="workflow",
        )
        scheduled += 1

    logger.info(
        "trigger_appointment: institution=%s appt=%s scheduled=%d skipped=%d",
        institution_id, appointment_id, scheduled, skipped,
    )
    return {"appointment_id": appointment_id, "scheduled": scheduled, "skipped": skipped}


# ---------------------------------------------------------------------------
# Callback trigger — AI Callback (Plan 07)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.trigger_callback_workflows",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def trigger_callback_workflows(
    self,
    *,
    institution_id: str,
    call_id: str,
    contact_id: str | None = None,
    location_id: str | None = None,
    preferred_callback_at_iso: str | None = None,
    trigger_metadata: dict | None = None,
) -> dict:
    """Find active callback_requested workflows and schedule an AI callback.

    Enqueued from the Retell webhook when an inbound call is classified
    needs_callback (and the clinic has opted in by activating such a workflow).
    Each matching workflow gets an enroll_and_start_workflow_run scheduled at the
    patient's requested callback time (or immediately if none / already passed).
    """
    _ensure_db()
    try:
        return asyncio.run(
            _trigger_callback_async(
                institution_id=institution_id,
                call_id=call_id,
                contact_id=contact_id,
                location_id=location_id,
                preferred_callback_at_iso=preferred_callback_at_iso,
                trigger_metadata=trigger_metadata or {},
            )
        )
    except Exception as exc:
        logger.exception(
            "trigger_callback_workflows failed: institution=%s call=%s: %s",
            institution_id, call_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _trigger_callback_async(
    *,
    institution_id: str,
    call_id: str,
    contact_id: str | None,
    location_id: str | None,
    preferred_callback_at_iso: str | None,
    trigger_metadata: dict,
) -> dict:
    now = datetime.now(tz=timezone.utc)

    preferred_at: datetime | None = None
    if preferred_callback_at_iso:
        preferred_at = datetime.fromisoformat(preferred_callback_at_iso)
        if preferred_at.tzinfo is None:
            preferred_at = preferred_at.replace(tzinfo=timezone.utc)

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"callback_trigger:{call_id}",
    ) as session:
        svc = CallbackTriggerService(session)
        workflows = await svc.find_active_callback_workflows(institution_id)

    eta = compute_callback_eta(preferred_at, now)

    scheduled = 0
    for wf in workflows:
        if not wf.current_version_id:
            continue
        idempotency_key = make_callback_idempotency_key(str(wf.current_version_id), call_id)
        enroll_and_start_workflow_run.apply_async(
            kwargs={
                "institution_id": institution_id,
                "workflow_id": str(wf.id),
                "workflow_version_id": str(wf.current_version_id),
                "contact_id": contact_id,
                "location_id": location_id,
                "trigger_type": "callback_requested",
                "trigger_ref_type": "call",
                "trigger_ref_id": call_id,
                "idempotency_key": idempotency_key,
                "trigger_metadata": {
                    **trigger_metadata,
                    "call_id": call_id,
                    "preferred_callback_at": preferred_callback_at_iso,
                },
            },
            eta=eta,  # None → runs immediately
            queue="workflow",
        )
        scheduled += 1

    logger.info(
        "trigger_callback: institution=%s call=%s scheduled=%d eta=%s",
        institution_id, call_id, scheduled, eta.isoformat() if eta else "now",
    )
    return {"call_id": call_id, "scheduled": scheduled}


# ---------------------------------------------------------------------------
# Recall scanner — Slice 11 (Plan 09)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.scan_recall_workflows",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def scan_recall_workflows(self) -> dict:
    """Find active recall_scan workflows and trigger patient enrollment.

    Stub: identifies institutions with active recall workflows and emits a
    per-institution scan task. The actual patient query (patients overdue for
    a visit by recall_interval_months) requires NexHealth patient/appointment
    history data which is resolved in a later Plan 09 slice once the
    NexHealth sync layer is in place.
    """
    _ensure_db()
    try:
        return asyncio.run(_scan_recall_async())
    except Exception as exc:
        logger.exception("scan_recall_workflows failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _scan_recall_async() -> dict:
    from sqlalchemy import select as sa_select

    from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus

    async with get_system_db_session(
        "celery", external_id="recall_scanner"
    ) as session:
        result = await session.execute(
            sa_select(AutomationWorkflow).where(
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        rows = [
            (wf.institution_id, wf.id, wf.current_version_id)
            for wf in result.scalars().all()
            if wf.trigger_type == "recall_scan"
        ]

    institution_workflow_counts: dict[str, int] = {}
    for institution_id, _wf_id, _version_id in rows:
        institution_workflow_counts[str(institution_id)] = (
            institution_workflow_counts.get(str(institution_id), 0) + 1
        )

    # NOTE: Real recall enrollment requires querying patient visit history from
    # NexHealth per institution. Stub here logs discovered workflows and returns
    # a summary. Wire in per-institution scan tasks when NexHealth sync is ready.
    logger.info(
        "scan_recall_workflows: found %d active recall workflows across %d institution(s)",
        len(rows), len(institution_workflow_counts),
    )
    return {
        "active_recall_workflows": len(rows),
        "institutions": len(institution_workflow_counts),
    }
