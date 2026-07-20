"""Celery tasks for the automation workflow engine scheduler."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from src.app.database import get_system_db_session, init_database, is_database_initialized
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationTimerStatus,
    AutomationWorkflow,
    AutomationWorkflowRun,
    AutomationWorkflowStatus,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.services.automation.appointment_trigger_service import (
    AppointmentTriggerService,
    compute_enrollment_eta,
    make_appointment_idempotency_key,
    make_recall_idempotency_key,
)
from src.app.services.automation.callback_trigger_service import (
    CallbackTriggerService,
    compute_callback_eta,
    make_callback_idempotency_key,
)
from src.app.services.automation.definition_schema import ConditionNode, WaitNode, WorkflowDefinition
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.nexhealth_backfill_service import (
    AppointmentSyncSummary,
    NexHealthAppointmentSyncService,
    NexHealthPatientSyncService,
    PatientSyncSummary,
)
from src.app.services.automation.nexhealth_subscription_service import (
    NexHealthSubscriptionLifecycleService,
)
from src.app.services.automation.nexhealth_sync_status_service import (
    NexHealthSyncStatusService,
)
from src.app.services.automation.revalidation import PmsLiveRevalidationService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.step_dispatcher import build_dispatcher
from src.app.services.automation.voice_attempt_recorder import stamp_attempt_outcome
from src.app.services.dead_letter import capture_dead_letter
from src.app.worker import celery_app

logger = logging.getLogger(__name__)

_CLAIM_BATCH = 50
_CLAIM_TTL_SECONDS = 120
# How long to defer a waiting run whose workflow is currently paused.
_PAUSED_DEFER_SECONDS = 300

# Run statuses that can be advanced by a fired timer.
_ADVANCEABLE_STATUSES = frozenset({
    AutomationRunStatus.WAITING.value,
    AutomationRunStatus.RUNNING.value,
})

_APPOINTMENT_SYNC_LOOKAHEAD_DAYS = 90


def _ensure_db() -> None:
    from src.app.config import settings
    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


def _merge_sync_summary(total: AppointmentSyncSummary, part: AppointmentSyncSummary) -> None:
    total.locations_scanned += part.locations_scanned
    total.appointments_seen += part.appointments_seen
    total.projected += part.projected
    total.triggered += part.triggered
    total.cancelled_runs += part.cancelled_runs
    total.failed_locations += part.failed_locations


def _merge_patient_sync_summary(total: PatientSyncSummary, part: PatientSyncSummary) -> None:
    total.locations_scanned += part.locations_scanned
    total.patients_seen += part.patients_seen
    total.projected += part.projected
    total.failed_locations += part.failed_locations


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
        if self.request.retries >= self.max_retries:
            # Retries exhausted — route to the dead-letter queue for operator replay
            # (payload is ids only, PHI-free).
            asyncio.run(
                capture_dead_letter(
                    source="workflow_dispatch",
                    event_type="dispatch_workflow_timer",
                    error=exc,
                    payload={"timer_id": timer_id, "run_id": run_id},
                    attempts=self.request.retries + 1,
                    institution_id=institution_id,
                    location_id=location_id,
                )
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

        # If the workflow is paused, defer this waiting run instead of advancing
        # it. Pause must stop in-flight runs, not just new enrollments; re-arm the
        # timer for a later poll so the run resumes once the workflow is active.
        workflow = await session.get(AutomationWorkflow, run.workflow_id)
        if workflow is not None and workflow.status == AutomationWorkflowStatus.PAUSED.value:
            svc = AutomationWorkflowSchedulerService(session)
            await svc.reschedule_timer(
                timer,
                due_at=datetime.now(tz=timezone.utc)
                + timedelta(seconds=_PAUSED_DEFER_SECONDS),
            )
            await session.commit()
            logger.info(
                "dispatch: workflow %s paused — deferred run %s", run.workflow_id, run_id
            )
            return {"skipped": True, "reason": "workflow paused", "deferred": True}

        # Load workflow version and parse definition.
        version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            logger.error("dispatch: version %s not found for run %s", run.workflow_version_id, run_id)
            return {"skipped": True, "reason": "version not found"}

        definition = WorkflowDefinition.model_validate(version.definition)

        # Build the dispatcher (real compliance gate + resolved location timezone)
        # via the single wiring path, then fire the timer before dispatch.
        # Inject the live PMS revalidator so an appointment-triggered run is
        # re-checked against NexHealth immediately before send (skips cancelled/
        # rescheduled appointments); no-op for recall/manual runs.
        dispatcher, location_timezone = await build_dispatcher(
            session,
            location_id=run.location_id,
            revalidator=PmsLiveRevalidationService(session),
        )
        await dispatcher.scheduler.fire_timer(timer)

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


# ---------------------------------------------------------------------------
# Stale-claim recovery task — runs on Celery beat, faster than the claim TTL
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.recover_stale_workflow_timers",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def recover_stale_workflow_timers(self) -> dict:
    """Reset timers claimed by a worker that crashed before firing them.

    Without this, a crash in the window between claim and dispatch strands a timer
    in CLAIMED forever and its run silently never fires — defeating the durable
    scheduler's core guarantee. Scheduled more frequently than the claim TTL.
    """
    _ensure_db()
    try:
        return asyncio.run(_recover_stale_async())
    except Exception as exc:
        logger.exception("recover_stale_workflow_timers failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


async def _recover_stale_async() -> dict:
    async with get_system_db_session(
        "celery", external_id="workflow_stale_recovery"
    ) as session:
        svc = AutomationWorkflowSchedulerService(session)
        count = await svc.recover_stale_claims()
        await session.commit()
    logger.info("recover_stale_workflow_timers: recovered %d timer(s)", count)
    return {"recovered": count}


@celery_app.task(
    name="src.app.tasks.automation_workflow.publish_workflow_metrics",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def publish_workflow_metrics(self) -> dict:
    """Emit workflow-engine health metrics to CloudWatch on Celery beat.

    Thin wrapper around the ``publish_workflow_metrics`` script so backlog,
    stale-timer, and failure signals surface as CloudWatch alarms.
    """
    _ensure_db()
    try:
        from src.app.scripts.publish_workflow_metrics import (
            publish_workflow_metrics as _publish,
        )

        return asyncio.run(_publish())
    except Exception as exc:
        logger.exception("publish_workflow_metrics failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


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
        if self.request.retries >= self.max_retries:
            asyncio.run(
                capture_dead_letter(
                    source="workflow_enroll",
                    event_type="enroll_and_start_workflow_run",
                    error=exc,
                    payload={
                        "workflow_id": workflow_id,
                        "contact_id": contact_id,
                        "idempotency_key": idempotency_key,
                    },
                    attempts=self.request.retries + 1,
                    institution_id=institution_id,
                    location_id=location_id,
                )
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

        definition = WorkflowDefinition.model_validate(version.definition)
        dispatcher, location_timezone = await build_dispatcher(
            session,
            location_id=location_id,
            revalidator=PmsLiveRevalidationService(session),
        )

        await dispatcher.runtime.start_run(run)
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
            str(wf.current_version_id), appointment_id, appointment_at_iso
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
# NexHealth subscription/backfill/reconciliation — Plan 09 resilient core
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.ensure_nexhealth_webhook_subscriptions",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def ensure_nexhealth_webhook_subscriptions(self) -> dict:
    """Ensure local subscription lifecycle rows and refresh health status."""
    _ensure_db()
    try:
        return asyncio.run(_ensure_nexhealth_webhook_subscriptions_async())
    except Exception as exc:
        logger.exception("ensure_nexhealth_webhook_subscriptions failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _ensure_nexhealth_webhook_subscriptions_async() -> dict:
    from src.app.config import settings

    async with get_system_db_session(
        "celery", external_id="nexhealth_subscription_lifecycle"
    ) as session:
        svc = NexHealthSubscriptionLifecycleService(session)
        ensure_summary = await svc.ensure_for_configured_locations(
            callback_url=settings.nexhealth_webhook_callback_url,
        )
        health = await svc.health_check()
        await session.commit()

    return {
        **ensure_summary,
        "health_total": health.total,
        "health_active": health.active,
        "health_pending": health.pending,
        "health_disabled": health.disabled,
        "health_failed": health.failed,
        "stale_marked": health.stale_marked,
    }


@celery_app.task(
    name="src.app.tasks.automation_workflow.backfill_nexhealth_appointments",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def backfill_nexhealth_appointments(self) -> dict:
    """Initial REST backfill for configured NexHealth appointment subscriptions."""
    _ensure_db()
    try:
        return asyncio.run(_sync_nexhealth_appointments_async(mode="backfill"))
    except Exception as exc:
        logger.exception("backfill_nexhealth_appointments failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


@celery_app.task(
    name="src.app.tasks.automation_workflow.reconcile_nexhealth_appointments",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def reconcile_nexhealth_appointments(self) -> dict:
    """Paced reconciliation sweep repairing stale/missing appointment projection rows."""
    _ensure_db()
    try:
        return asyncio.run(_sync_nexhealth_appointments_async(mode="reconciliation"))
    except Exception as exc:
        logger.exception("reconcile_nexhealth_appointments failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


@celery_app.task(
    name="src.app.tasks.automation_workflow.backfill_nexhealth_patients",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def backfill_nexhealth_patients(self) -> dict:
    """Initial REST backfill for configured NexHealth patient/contact projections."""
    _ensure_db()
    try:
        return asyncio.run(_sync_nexhealth_patients_async(mode="backfill"))
    except Exception as exc:
        logger.exception("backfill_nexhealth_patients failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


@celery_app.task(
    name="src.app.tasks.automation_workflow.reconcile_nexhealth_patients",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def reconcile_nexhealth_patients(self) -> dict:
    """Paced reconciliation sweep repairing stale/missing patient projections."""
    _ensure_db()
    try:
        return asyncio.run(_sync_nexhealth_patients_async(mode="reconciliation"))
    except Exception as exc:
        logger.exception("reconcile_nexhealth_patients failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


@celery_app.task(
    name="src.app.tasks.automation_workflow.poll_nexhealth_sync_statuses",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def poll_nexhealth_sync_statuses(self) -> dict:
    """Poll NexHealth PMS read/write sync health for configured locations."""
    _ensure_db()
    try:
        return asyncio.run(_poll_nexhealth_sync_statuses_async())
    except Exception as exc:
        logger.exception("poll_nexhealth_sync_statuses failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _poll_nexhealth_sync_statuses_async() -> dict:
    async with get_system_db_session(
        "celery", external_id="nexhealth_sync_status_poll"
    ) as session:
        summary = await NexHealthSyncStatusService(session).poll_all_configured_locations()
        await session.commit()

    logger.info(
        "poll_nexhealth_sync_statuses: locations=%d updated=%d failed=%d",
        summary.locations_checked,
        summary.updated,
        summary.failed_locations,
    )
    return {
        "locations_checked": summary.locations_checked,
        "updated": summary.updated,
        "failed_locations": summary.failed_locations,
    }


async def _sync_nexhealth_patients_async(*, mode: str) -> dict:
    async with get_system_db_session(
        "celery", external_id=f"nexhealth_patient_{mode}_target_scan"
    ) as session:
        targets = await NexHealthSubscriptionLifecycleService(session).active_or_pending_targets()

    total = PatientSyncSummary()
    for institution_id, subscription_id in targets:
        async with get_system_db_session(
            "celery",
            institution_id=institution_id,
            external_id=f"nexhealth_patient_{mode}:{subscription_id}",
        ) as session:
            svc = NexHealthPatientSyncService(session)
            part = await svc.sync_subscription(
                subscription_id=subscription_id,
                mode="backfill" if mode == "backfill" else "reconciliation",
            )
            await session.commit()
            _merge_patient_sync_summary(total, part)

    logger.info(
        "nexhealth_patient_%s: subscriptions=%d locations=%d patients=%d projected=%d failed_locations=%d",
        mode,
        len(targets),
        total.locations_scanned,
        total.patients_seen,
        total.projected,
        total.failed_locations,
    )
    return {
        "mode": mode,
        "subscriptions": len(targets),
        "locations_scanned": total.locations_scanned,
        "patients_seen": total.patients_seen,
        "projected": total.projected,
        "failed_locations": total.failed_locations,
    }


async def _sync_nexhealth_appointments_async(*, mode: str) -> dict:
    async with get_system_db_session(
        "celery", external_id=f"nexhealth_{mode}_target_scan"
    ) as session:
        targets = await NexHealthSubscriptionLifecycleService(session).active_or_pending_targets()

    total = AppointmentSyncSummary()
    for institution_id, subscription_id in targets:
        async with get_system_db_session(
            "celery",
            institution_id=institution_id,
            external_id=f"nexhealth_{mode}:{subscription_id}",
        ) as session:
            svc = NexHealthAppointmentSyncService(session)
            part = await svc.sync_subscription(
                subscription_id=subscription_id,
                mode="backfill" if mode == "backfill" else "reconciliation",
                lookahead_days=_APPOINTMENT_SYNC_LOOKAHEAD_DAYS,
            )
            await session.commit()
            _merge_sync_summary(total, part)

    logger.info(
        "nexhealth_%s: subscriptions=%d locations=%d appointments=%d projected=%d triggered=%d cancelled_runs=%d failed_locations=%d",
        mode,
        len(targets),
        total.locations_scanned,
        total.appointments_seen,
        total.projected,
        total.triggered,
        total.cancelled_runs,
        total.failed_locations,
    )
    return {
        "mode": mode,
        "subscriptions": len(targets),
        "locations_scanned": total.locations_scanned,
        "appointments_seen": total.appointments_seen,
        "projected": total.projected,
        "triggered": total.triggered,
        "cancelled_runs": total.cancelled_runs,
        "failed_locations": total.failed_locations,
    }


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

    from src.app.models.call import Call
    from src.app.models.contact import Contact
    from src.app.models.sms_consent import (
        ConsentBasis,
        ConsentChannel,
        ConsentSource,
        ConsentStatus,
    )
    from src.app.services.sms_compliance import SmsComplianceService

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"callback_trigger:{call_id}",
    ) as session:
        # Double-contact guard (CB-2): if staff already resolved this callback in the
        # manual queue, don't also AI-dial. (Residual: a resolve during the ETA delay
        # is not caught here.)
        call = await session.get(Call, call_id)
        if call is None or call.callback_resolved:
            logger.info(
                "trigger_callback: skip institution=%s call=%s (missing or already resolved)",
                institution_id, call_id,
            )
            return {"call_id": call_id, "scheduled": 0, "skipped": "resolved_or_missing"}

        svc = CallbackTriggerService(session)
        workflows = await svc.find_active_callback_workflows(institution_id)

        # Consent capture (XC-6 / CB-3): a patient's inbound request to be called back is
        # an express basis to place that AI callback. Record a granted VOICE consent so the
        # compliance gate permits the outbound voice call — but ONLY if no voice consent
        # record exists yet, so a prior opt-out (REVOKED) is never overwritten and rows
        # don't accumulate. LEGAL-REVIEW NOTE: treats the inbound callback request as express
        # voice consent for this callback.
        if workflows and contact_id:
            contact = await session.get(Contact, contact_id)
            phone = contact.phone if contact else None
            if phone:
                comp = SmsComplianceService(session)
                if not await comp.has_consent_record(institution_id, phone, ConsentChannel.VOICE):
                    await comp.record_consent(
                        institution_id=institution_id,
                        phone=phone,
                        status=ConsentStatus.GRANTED,
                        channel=ConsentChannel.VOICE,
                        basis=ConsentBasis.EXPRESS,  # patient-initiated request = express basis
                        location_id=location_id,
                        contact_id=contact_id,
                        source=ConsentSource.SYSTEM,
                        reason="inbound_callback_request",
                    )

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
# Voice outcome resume — AI Voice outcome-feedback loop (Plan 03)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.resume_voice_outcome",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def resume_voice_outcome(
    self,
    *,
    institution_id: str,
    retell_call_id: str,
    call_outcome: str,
    disconnection_reason: str | None = None,
) -> dict:
    """Resume a run parked WAITING for a voice-call outcome (Plan 03 §7.2).

    Enqueued from the Retell post-call webhook for outbound calls. Finds the parked
    voice step by retell_call_id, writes ``call_outcome`` into the run context, cancels
    the safety-timeout timer, and resumes the run so a downstream ConditionNode can
    branch (no-answer→retry, voicemail→SMS, answered→done). No-ops if no parked step
    matches (e.g. a fire-and-forget or non-campaign outbound call).
    """
    _ensure_db()
    try:
        return asyncio.run(
            _resume_voice_outcome_async(
                institution_id=institution_id,
                retell_call_id=retell_call_id,
                call_outcome=call_outcome,
                disconnection_reason=disconnection_reason,
            )
        )
    except Exception as exc:
        logger.exception(
            "resume_voice_outcome failed: institution=%s call=%s: %s",
            institution_id, retell_call_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _resume_voice_outcome_async(
    *,
    institution_id: str,
    retell_call_id: str,
    call_outcome: str,
    disconnection_reason: str | None = None,
) -> dict:
    from sqlalchemy import select

    from src.app.models.automation_workflow import (
        AutomationStepStatus,
        AutomationWorkflowStepExecution,
    )
    from src.app.services.automation.voice_node_executor import _CALL_PLACED_AWAITING

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"voice_outcome:{retell_call_id}",
    ) as session:
        # Find the parked voice step by retell_call_id (dialect-safe: filter in Python
        # over the few awaiting steps rather than a JSON query).
        rows = (
            await session.execute(
                select(AutomationWorkflowStepExecution).where(
                    AutomationWorkflowStepExecution.institution_id == institution_id,
                    AutomationWorkflowStepExecution.status == AutomationStepStatus.WAITING.value,
                    AutomationWorkflowStepExecution.result_code == _CALL_PLACED_AWAITING,
                )
            )
        ).scalars().all()
        step = next(
            (s for s in rows if (s.result_metadata or {}).get("retell_call_id") == retell_call_id),
            None,
        )
        if step is None:
            return {"resumed": False, "reason": "no_parked_step"}

        run = await session.get(AutomationWorkflowRun, step.workflow_run_id)
        if run is None or run.status != AutomationRunStatus.WAITING.value:
            return {"resumed": False, "reason": "run_not_waiting"}

        # Cancel the safety-timeout timer (best-effort); the run.status==WAITING guard
        # in resume_after_timer makes a timer/webhook race at-most-once regardless.
        await AutomationWorkflowSchedulerService(session).cancel_timers_for_run(run.id)

        # Write the outcome into the run context so the downstream branch reads it.
        from src.app.services.automation.campaign_response_service import (
            CampaignResponseService,
        )

        await CampaignResponseService(session).record_voice_response(
            institution_id=institution_id,
            retell_call_id=retell_call_id,
            call_outcome=call_outcome,
            disconnection_reason=disconnection_reason,
        )
        md = dict(run.trigger_metadata or {})
        md["call_outcome"] = call_outcome
        run.trigger_metadata = md
        await session.flush()

        # Resolve the voice-attempt row (V-4) to COMPLETED with its dial outcome so
        # the attempt/outcome history reflects how the call went (best-effort).
        await stamp_attempt_outcome(
            session,
            institution_id=institution_id,
            retell_call_id=retell_call_id,
            dial_outcome=call_outcome,
            disconnection_reason=disconnection_reason,
        )

        version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            return {"resumed": False, "reason": "version_not_found"}
        definition = WorkflowDefinition.model_validate(version.definition)

        dispatcher, location_timezone = await build_dispatcher(
            session,
            location_id=run.location_id,
            revalidator=PmsLiveRevalidationService(session),
        )
        result = await dispatcher.resume_after_timer(
            run, definition, context=md, location_timezone=location_timezone
        )
        await session.commit()

    logger.info(
        "resume_voice_outcome: institution=%s call=%s outcome=%s status=%s",
        institution_id, retell_call_id, call_outcome, result.status,
    )
    return {"resumed": True, "status": result.status, "call_outcome": call_outcome}


# ---------------------------------------------------------------------------
# Context-field resume — SMS confirmations + reactivation booking detection
# ---------------------------------------------------------------------------


def _waiting_step_targets_field(
    definition: WorkflowDefinition, current_step_id: str | None, field: str
) -> bool:
    """True when the current WaitNode flows into a ConditionNode reading field."""
    if not current_step_id:
        return False
    node_map = {node.id: node for node in definition.nodes}
    current = node_map.get(current_step_id)
    if not isinstance(current, WaitNode):
        return False
    next_node = node_map.get(current.next_node_id)
    if not isinstance(next_node, ConditionNode):
        return False
    return any(rule.field == field for rule in next_node.rules)


@celery_app.task(
    name="src.app.tasks.automation_workflow.resume_sms_confirmation",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def resume_sms_confirmation(
    self,
    *,
    institution_id: str,
    location_id: str,
    from_number: str,
    body: str,
    message_sid: str | None = None,
) -> dict:
    """Resume a WAITING confirmation run from a patient's inbound SMS reply."""
    _ensure_db()
    try:
        return asyncio.run(
            _resume_sms_confirmation_async(
                institution_id=institution_id,
                location_id=location_id,
                from_number=from_number,
                body=body,
                message_sid=message_sid,
            )
        )
    except Exception as exc:
        logger.exception(
            "resume_sms_confirmation failed: institution=%s location=%s: %s",
            institution_id, location_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _resume_sms_confirmation_async(
    *,
    institution_id: str,
    location_id: str,
    from_number: str,
    body: str,
    message_sid: str | None = None,
) -> dict:
    from sqlalchemy import select

    from src.app.models.contact import Contact

    phone_hash = Contact.find_by_phone_hash(from_number)
    if not phone_hash:
        return {"resumed": 0, "reason": "phone_hash_unavailable"}

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=f"sms_confirmation:{message_sid or phone_hash}",
    ) as session:
        contacts = (
            await session.execute(
                select(Contact.id).where(
                    Contact.institution_id == institution_id,
                    Contact.phone_hash == phone_hash,
                )
            )
        ).scalars().all()
        if not contacts:
            return {"resumed": 0, "reason": "contact_not_found"}

        result = await _resume_waiting_runs_for_context_field(
            session=session,
            institution_id=institution_id,
            location_id=location_id,
            contact_ids=[str(contact_id) for contact_id in contacts],
            context_field="appointment_status",
            context_value="confirmed",
            metadata_updates={
                "appointment_status": "confirmed",
                "sms_confirmation_reply": body.strip(),
                "sms_confirmation_message_sid": message_sid,
            },
        )

        confirmed = result.get("outcomes", {}).get("confirmed", 0)
        if confirmed:
            await _confirm_appointments_for_runs(
                session,
                institution_id=institution_id,
                location_id=location_id,
                runs=result["outcome_runs"].get("confirmed", []),
            )
        await session.commit()
        return {
            "resumed": result["resumed"],
            "matched": result["matched"],
            "outcomes": result["outcomes"],
        }


@celery_app.task(
    name="src.app.tasks.automation_workflow.resume_reactivation_booking",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def resume_reactivation_booking(
    self,
    *,
    institution_id: str,
    location_id: str,
    contact_id: str,
    appointment_id: str,
) -> dict:
    """Resume WAITING reactivation runs when NexHealth reports a new booking."""
    _ensure_db()
    try:
        return asyncio.run(
            _resume_reactivation_booking_async(
                institution_id=institution_id,
                location_id=location_id,
                contact_id=contact_id,
                appointment_id=appointment_id,
            )
        )
    except Exception as exc:
        logger.exception(
            "resume_reactivation_booking failed: institution=%s location=%s contact=%s: %s",
            institution_id, location_id, contact_id, exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _resume_reactivation_booking_async(
    *,
    institution_id: str,
    location_id: str,
    contact_id: str,
    appointment_id: str,
) -> dict:
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=f"reactivation_booking:{appointment_id}",
    ) as session:
        result = await _resume_waiting_runs_for_context_field(
            session=session,
            institution_id=institution_id,
            location_id=location_id,
            contact_ids=[contact_id],
            context_field="appointment_booked",
            context_value=True,
            metadata_updates={
                "appointment_booked": True,
                "booked_appointment_id": appointment_id,
            },
        )
        await session.commit()
        return {
            "resumed": result["resumed"],
            "matched": result["matched"],
            "outcomes": result["outcomes"],
        }


async def _resume_waiting_runs_for_context_field(
    *,
    session,
    institution_id: str,
    location_id: str,
    contact_ids: list[str],
    context_field: str,
    context_value,
    metadata_updates: dict,
) -> dict:
    from sqlalchemy import select

    if not contact_ids:
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    rows = (
        await session.execute(
            select(AutomationWorkflowRun).where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.location_id == location_id,
                AutomationWorkflowRun.contact_id.in_(contact_ids),
                AutomationWorkflowRun.status == AutomationRunStatus.WAITING.value,
            )
        )
    ).scalars().all()

    scheduler = AutomationWorkflowSchedulerService(session)
    matched = 0
    resumed = 0
    outcomes: dict[str, int] = {}
    outcome_runs: dict[str, list[AutomationWorkflowRun]] = {}

    for run in rows:
        version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            continue
        definition = WorkflowDefinition.model_validate(version.definition)
        if not _waiting_step_targets_field(definition, run.current_step_id, context_field):
            continue

        matched += 1
        await scheduler.cancel_timers_for_run(run.id)
        md = dict(run.trigger_metadata or {})
        md.update({k: v for k, v in metadata_updates.items() if v is not None})
        md[context_field] = context_value
        run.trigger_metadata = md
        await session.flush()

        dispatcher, location_timezone = await build_dispatcher(
            session,
            location_id=run.location_id,
            revalidator=PmsLiveRevalidationService(session),
        )
        result = await dispatcher.resume_after_timer(
            run, definition, context=md, location_timezone=location_timezone
        )
        if result.status == "completed":
            resumed += 1
            if result.outcome:
                outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
                outcome_runs.setdefault(result.outcome, []).append(run)

    return {
        "matched": matched,
        "resumed": resumed,
        "outcomes": outcomes,
        "outcome_runs": outcome_runs,
    }


async def _confirm_appointments_for_runs(
    session,
    *,
    institution_id: str,
    location_id: str,
    runs: list[AutomationWorkflowRun],
) -> None:
    from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation
    from src.app.pms.base import SupportsAppointmentConfirmation
    from src.app.pms.nexhealth.adapter import NexHealthAdapter
    from src.app.services.audit import log_audit
    from src.app.services.sms_privacy import safe_error_summary

    institution = await session.get(Institution, institution_id)
    location = await session.get(InstitutionLocation, location_id)
    if institution is None or location is None:
        return

    adapter = None
    try:
        adapter = await NexHealthAdapter.create(institution, location)
        if not isinstance(adapter, SupportsAppointmentConfirmation):
            for run in runs:
                await log_audit(
                    actor=AuditActor.SYSTEM,
                    action=AuditAction.CONFIRM_APPOINTMENT,
                    target_resource=f"appointment:{run.trigger_ref_id or 'unknown'}",
                    outcome=AuditOutcome.FAILURE_VALIDATION,
                    metadata={
                        "source": "automation_sms_confirmation",
                        "reason": "unsupported_pms_capability",
                        "workflow_run_id": str(run.id),
                    },
                    institution_id=institution_id,
                    location_id=location_id,
                )
            return

        for run in runs:
            if run.trigger_ref_type != "appointment" or not run.trigger_ref_id:
                continue
            result = await adapter.confirm_appointment(str(run.trigger_ref_id))
            await log_audit(
                actor=AuditActor.SYSTEM,
                action=AuditAction.CONFIRM_APPOINTMENT,
                target_resource=f"appointment:{run.trigger_ref_id}",
                outcome=(
                    AuditOutcome.SUCCESS
                    if result.success
                    else AuditOutcome.FAILURE_EXTERNAL_API
                ),
                metadata={
                    "source": "automation_sms_confirmation",
                    "workflow_run_id": str(run.id),
                    "pms_status": result.status,
                    "error": safe_error_summary(result.error) if result.error else None,
                },
                institution_id=institution_id,
                location_id=location_id,
            )
    except Exception as exc:  # noqa: BLE001 - write-back must fail open.
        for run in runs:
            await log_audit(
                actor=AuditActor.SYSTEM,
                action=AuditAction.CONFIRM_APPOINTMENT,
                target_resource=f"appointment:{run.trigger_ref_id or 'unknown'}",
                outcome=AuditOutcome.FAILURE_EXTERNAL_API,
                metadata={
                    "source": "automation_sms_confirmation",
                    "workflow_run_id": str(run.id),
                    "error": safe_error_summary(exc),
                },
                institution_id=institution_id,
                location_id=location_id,
            )
    finally:
        if adapter is not None:
            await adapter.close()


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
    """Enroll patients overdue for recall into active recall_scan workflows.

    For each institution with active recall workflows, pulls the patient recall
    queue from NexHealth per configured location (paced/jittered so the shared
    NexHealth key is not hammered), derives overdue patients from their recall
    due date, and enqueues ``enroll_and_start_workflow_run`` per (patient,
    workflow) with a stable ``recall:{version}:{patient}:{period}`` idempotency
    key so a persistently-overdue patient is enrolled at most once per period.
    """
    _ensure_db()
    try:
        return asyncio.run(_scan_recall_async())
    except Exception as exc:
        logger.exception("scan_recall_workflows failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


# Per-institution pacing between NexHealth recall pulls (jittered) so the
# shared API key is not saturated when many institutions scan on the same beat.
_RECALL_PACING_MIN_SECONDS = 0.5
_RECALL_PACING_MAX_SECONDS = 2.0


def _recall_patient_id(recall: dict) -> str | None:
    """Extract the NexHealth patient id from a recall record."""
    pid = recall.get("patient_id")
    if pid is None:
        patient = recall.get("patient")
        if isinstance(patient, dict):
            pid = patient.get("id")
    return str(pid) if pid not in (None, "") else None


def _recall_is_due(recall: dict, *, now: datetime) -> bool:
    """A recall is due when it has no future due date (overdue / due today).

    Records with a due date strictly in the future are skipped; a missing/
    unparseable due date is treated as due (the record is on the recall queue).
    """
    raw = recall.get("due_date") or recall.get("due") or recall.get("next_visit_date")
    if not raw:
        return True
    try:
        due = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= now


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
        by_institution: dict[str, list[dict]] = {}
        for wf in result.scalars().all():
            if wf.trigger_type != "recall_scan":
                continue
            by_institution.setdefault(str(wf.institution_id), []).append(
                {
                    "workflow_id": str(wf.id),
                    "version_id": str(wf.current_version_id),
                }
            )

    active_workflows = sum(len(w) for w in by_institution.values())
    total_enrolled = 0
    for idx, (institution_id, workflows) in enumerate(by_institution.items()):
        if idx > 0:
            # Jittered pacing between institutions to spread load on the shared key.
            await asyncio.sleep(
                random.uniform(_RECALL_PACING_MIN_SECONDS, _RECALL_PACING_MAX_SECONDS)
            )
        try:
            total_enrolled += await _enroll_recalls_for_institution(
                institution_id, workflows
            )
        except Exception as exc:  # noqa: BLE001 — one institution must not abort the sweep
            logger.exception(
                "scan_recall_workflows: institution=%s failed: %s", institution_id, exc
            )

    logger.info(
        "scan_recall_workflows: institutions=%d workflows=%d enrolled=%d",
        len(by_institution), active_workflows, total_enrolled,
    )
    return {
        "active_recall_workflows": active_workflows,
        "institutions": len(by_institution),
        "enrolled": total_enrolled,
    }


async def _enroll_recalls_for_institution(
    institution_id: str, workflows: list[dict]
) -> int:
    """Pull NexHealth recalls for an institution's locations and enqueue enrollments.

    Returns the number of enrollment tasks enqueued.
    """
    from sqlalchemy import select as sa_select

    from src.app.models.contact import Contact
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation
    from src.app.pms.nexhealth.adapter import NexHealthAdapter

    now = datetime.now(tz=timezone.utc)
    period = now.strftime("%Y-%m")
    enrolled = 0

    async with get_system_db_session(
        "celery", institution_id=institution_id, external_id=f"recall_scan:{institution_id}"
    ) as session:
        institution = await session.get(Institution, institution_id)
        if institution is None:
            return 0

        loc_result = await session.execute(
            sa_select(InstitutionLocation).where(
                InstitutionLocation.institution_id == institution_id,
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        locations = list(loc_result.scalars().all())

        for location in locations:
            try:
                adapter = await NexHealthAdapter.create(institution, location)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recall_scan: adapter build failed inst=%s loc=%s: %s",
                    institution_id, location.id, exc,
                )
                continue
            try:
                recalls = await adapter.list_patient_recalls()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recall_scan: recall pull failed inst=%s loc=%s: %s",
                    institution_id, location.id, exc,
                )
                continue
            finally:
                await adapter.close()

            for recall in recalls:
                patient_id = _recall_patient_id(recall)
                if not patient_id or not _recall_is_due(recall, now=now):
                    continue

                contact_row = await session.execute(
                    sa_select(Contact).where(
                        Contact.institution_id == institution_id,
                        Contact.nexhealth_patient_id == patient_id,
                    )
                )
                contact = contact_row.scalar_one_or_none()
                contact_id = str(contact.id) if contact else None

                for wf in workflows:
                    key = make_recall_idempotency_key(wf["version_id"], patient_id, period)
                    enroll_and_start_workflow_run.apply_async(
                        kwargs={
                            "institution_id": institution_id,
                            "workflow_id": wf["workflow_id"],
                            "workflow_version_id": wf["version_id"],
                            "contact_id": contact_id,
                            "location_id": str(location.id),
                            "trigger_type": "recall_scan",
                            "trigger_ref_type": "recall",
                            "trigger_ref_id": patient_id,
                            "idempotency_key": key,
                            "trigger_metadata": {
                                "nexhealth_patient_id": patient_id,
                                "recall_due_date": recall.get("due_date"),
                                "recall_period": period,
                            },
                        },
                        queue="workflow",
                    )
                    enrolled += 1

    return enrolled
