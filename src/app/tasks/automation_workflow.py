"""Celery tasks for the automation workflow engine scheduler."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
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
    make_appointment_state_idempotency_key,
    make_recall_idempotency_key,
    workflow_matches_appointment,
    workflow_matches_appointment_state,
    workflow_matches_recall,
)
from src.app.services.automation.callback_trigger_service import (
    CallbackTriggerService,
    compute_callback_eta,
    make_callback_idempotency_key,
)
from src.app.services.automation.definition_schema import (
    ConditionNode,
    RetellSmsConversationNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_filter import trigger_filter_matches
from src.app.services.automation.enrollment_service import (
    AutomationWorkflowEnrollmentService,
)
from src.app.services.automation.gotracker_subscription_service import (
    GoTrackerSubscriptionLifecycleService,
)
from src.app.services.automation.nexhealth_backfill_service import (
    AppointmentSyncSummary,
    NexHealthAppointmentSyncService,
    NexHealthPatientSyncService,
    PatientSyncSummary,
)
from src.app.services.automation.nexhealth_subscription_service import (
    NexHealthSubscriptionLifecycleService,
)
from src.app.services.automation.nexhealth_shadow_webhook_service import (
    NexHealthWebhookShadowSubscriptionService,
)
from src.app.services.automation.nexhealth_sync_status_service import (
    NexHealthSyncStatusService,
)
from src.app.services.automation.patient_status_trigger_service import (
    PatientStatusTriggerService,
    patient_status_idempotency_key,
)
from src.app.pms.gotracker.statuses import is_non_attending_status
from src.app.pms.models import PatientCommunicationSnapshot, UniversalRecallType
from src.app.services.automation.revalidation import PmsLiveRevalidationService
from src.app.services.automation.scheduler_service import (
    AutomationWorkflowSchedulerService,
)
from src.app.services.automation.step_dispatcher import build_dispatcher
from src.app.services.automation.voice_attempt_recorder import stamp_attempt_outcome
from src.app.services.dead_letter import (
    capture_dead_letter,
    resolve_workflow_timer_dead_letters,
)
from src.app.services.patient_communication import (
    TREATMENT_PLAN_CONTEXT_FIELDS,
    patient_communication_workflow_context,
    patient_recall_from_raw,
    pms_context_requirements,
)
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


_OUTBOUND_LIMITS = None


def _outbound_limits():
    """One limiter per worker process, built on first use.

    Module-level rather than per-call so the Redis connection is reused, and
    lazy so importing this module does not require Redis to exist.
    """
    global _OUTBOUND_LIMITS
    if _OUTBOUND_LIMITS is None:
        from src.app.config import settings
        from src.app.services.outbound_limits import OutboundLimits, SendProvider

        _OUTBOUND_LIMITS = OutboundLimits(
            call_concurrency=settings.outbound_call_concurrency_limit,
            lease_seconds=settings.outbound_call_lease_seconds,
            provider_per_minute={
                SendProvider.TWILIO: settings.twilio_send_rate_per_minute,
                SendProvider.EMAIL: settings.email_send_rate_per_minute,
            },
        )
    return _OUTBOUND_LIMITS


# The Chair Flow label GoTracker emits when a visit finishes, and which the
# shipped post-op template triggers on. NexHealth has no completion event, so the
# post-visit sweep writes this same label — one template definition then enrols on
# either PMS. Matching is case-insensitive
# (`appointment_trigger_service.py:217-222`), but keep the exact casing.
NEXHEALTH_VISIT_COMPLETED = "Completed"

_CLAIM_BATCH = 50
_CLAIM_TTL_SECONDS = 120
# Keep one poll comfortably inside the 30-second beat so ticks never overlap.
_CLAIM_BUDGET_SECONDS = 20.0
# Backstop against an unbounded loop if timers are being re-queued as fast as
# they are claimed.
_MAX_CLAIM_ROUNDS = 40
# How long to defer a waiting run whose workflow is currently paused.
_PAUSED_DEFER_SECONDS = 300

# Run statuses that can be advanced by a fired timer.
_ADVANCEABLE_STATUSES = frozenset(
    {
        AutomationRunStatus.WAITING.value,
        AutomationRunStatus.RUNNING.value,
    }
)

_APPOINTMENT_SYNC_LOOKAHEAD_DAYS = 90
_RETELL_OUTCOME_POLL_BATCH = 25
_RETELL_OUTCOME_MIN_AGE_SECONDS = 30
_RETELL_TERMINAL_CALL_STATUSES = frozenset({"ended", "not_connected", "error"})
_GOTRACKER_WRITEBACK_STALE_SECONDS = 5 * 60
_GOTRACKER_WRITEBACK_SWEEP_BATCH = 25


def _superadmin_system_session(external_id: str):
    """DB session that can see every institution for trusted global scans."""
    return get_system_db_session(
        "user",
        role="SUPER_ADMIN",
        user_id="00000000-0000-0000-0000-000000000000",
        external_id=external_id,
    )


def _ensure_db() -> None:
    from src.app.config import settings

    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


def _merge_sync_summary(
    total: AppointmentSyncSummary, part: AppointmentSyncSummary
) -> None:
    total.locations_scanned += part.locations_scanned
    total.appointments_seen += part.appointments_seen
    total.projected += part.projected
    total.triggered += part.triggered
    total.cancelled_runs += part.cancelled_runs
    total.failed_locations += part.failed_locations


def _merge_patient_sync_summary(
    total: PatientSyncSummary, part: PatientSyncSummary
) -> None:
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
    """Drain due timers, rather than taking one fixed batch per tick.

    A fixed batch on a fixed beat is a hard throughput ceiling: 50 timers every
    30 seconds is ~100/minute for the whole platform, so one large recall
    campaign took minutes just to issue its first steps and every other tenant
    queued behind it. Claiming repeatedly until the backlog clears — or until
    the tick's budget is spent — removes the ceiling without making the beat
    itself faster.
    """
    total_claimed = 0
    rounds = 0
    started = time.monotonic()
    queue_depth = 0

    while rounds < _MAX_CLAIM_ROUNDS:
        now = datetime.now(tz=timezone.utc)
        async with _superadmin_system_session("workflow_scheduler_poll") as session:
            svc = AutomationWorkflowSchedulerService(session)
            timers = await svc.claim_due_timers(
                now=now, limit=_CLAIM_BATCH, claim_ttl_seconds=_CLAIM_TTL_SECONDS
            )
            claimed = [
                (t.id, t.institution_id, t.location_id, t.workflow_run_id)
                for t in timers
            ]
            await session.commit()

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

        total_claimed += len(claimed)
        rounds += 1
        # A short batch means the backlog is drained; nothing left to claim.
        if len(claimed) < _CLAIM_BATCH:
            break
        # Stop before the next beat would overlap this one, and report what is
        # left so a persistent backlog is visible rather than inferred.
        if time.monotonic() - started >= _CLAIM_BUDGET_SECONDS:
            async with _superadmin_system_session("workflow_scheduler_poll") as session:
                queue_depth = await AutomationWorkflowSchedulerService(
                    session
                ).count_due()
            break

    logger.info(
        "poll_workflow_timers: claimed %d timer(s) in %d round(s) remaining=%d",
        total_claimed,
        rounds,
        queue_depth,
    )
    return {"claimed": total_claimed, "rounds": rounds, "remaining": queue_depth}


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
        if (
            workflow is not None
            and workflow.status == AutomationWorkflowStatus.PAUSED.value
        ):
            svc = AutomationWorkflowSchedulerService(session)
            await svc.reschedule_timer(
                timer,
                due_at=datetime.now(tz=timezone.utc)
                + timedelta(seconds=_PAUSED_DEFER_SECONDS),
            )
            await session.commit()
            logger.info(
                "dispatch: workflow %s paused — deferred run %s",
                run.workflow_id,
                run_id,
            )
            return {"skipped": True, "reason": "workflow paused", "deferred": True}

        # Load workflow version and parse definition.
        version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            logger.error(
                "dispatch: version %s not found for run %s",
                run.workflow_version_id,
                run_id,
            )
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

    await resolve_workflow_timer_dead_letters(
        timer_id=timer_id,
        run_id=run_id,
        institution_id=institution_id,
        location_id=location_id,
    )
    _enqueue_patient_status_triggers(
        institution_id=institution_id,
        status_event_ids=result.patient_status_event_ids,
    )
    logger.info(
        "dispatch: timer=%s run=%s status=%s steps=%d",
        timer_id,
        run_id,
        result.status,
        result.steps_advanced,
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
    async with _superadmin_system_session("workflow_stale_recovery") as session:
        svc = AutomationWorkflowSchedulerService(session)
        count = await svc.recover_stale_claims()
        await session.commit()
    logger.info("recover_stale_workflow_timers: recovered %d timer(s)", count)
    return {"recovered": count}


# ---------------------------------------------------------------------------
# NexHealth post-visit completion sweeper
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.sweep_nexhealth_completed_visits",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def sweep_nexhealth_completed_visits(self) -> dict:
    """Mark finished NexHealth visits complete so post-visit campaigns can run.

    GoTracker reports visit progress through Chair Flow, and a transition to
    ``Completed`` is what enrols the post-op follow-up campaign. NexHealth has no
    equivalent — it emits no checkout, check-in or completion event — so the
    signal is derived here: once an appointment's start time plus its type's
    duration has passed and it was not cancelled, the visit is treated as done.

    Marking ``flow_state`` makes the row ineligible for the next sweep, and the
    enrollment idempotency key folds in ``flow_state``/``flow_changed_at``, so a
    repeated sweep cannot double-enrol.
    """
    _ensure_db()
    try:
        return asyncio.run(_sweep_nexhealth_completed_visits_async())
    except Exception as exc:
        logger.exception("sweep_nexhealth_completed_visits failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)


def completed_visit_candidates_query(window_start: datetime, now: datetime):
    """Candidate rows for the post-visit sweep, with their type duration.

    Extracted so the filters are testable on their own. Each predicate here is
    load-bearing: dropping the ``pms_type`` one would synthesize completion over
    real GoTracker Chair Flow data, and dropping the ``flow_state`` one would
    re-trigger every sweep.
    """
    from sqlalchemy import and_, or_, select

    from src.app.models.appointment_working_set import AppointmentWorkingSet
    from src.app.models.institution import Institution
    from src.app.models.institution_appointment_type import InstitutionAppointmentType

    return (
        select(AppointmentWorkingSet, InstitutionAppointmentType.duration_minutes)
        .join(Institution, Institution.id == AppointmentWorkingSet.institution_id)
        .outerjoin(
            InstitutionAppointmentType,
            and_(
                InstitutionAppointmentType.institution_id
                == AppointmentWorkingSet.institution_id,
                InstitutionAppointmentType.location_id
                == AppointmentWorkingSet.location_id,
                InstitutionAppointmentType.source == "nexhealth",
                InstitutionAppointmentType.source_id
                == AppointmentWorkingSet.appointment_type_id,
            ),
        )
        .where(
            # Only NexHealth institutions. GoTracker rows carry real Chair Flow
            # data and must never be synthesized over.
            Institution.pms_type == "nexhealth",
            AppointmentWorkingSet.status == "scheduled",
            AppointmentWorkingSet.start_time.is_not(None),
            # Cheap pre-filter; the exact end time is computed per row below
            # because duration varies by appointment type.
            AppointmentWorkingSet.start_time >= window_start,
            AppointmentWorkingSet.start_time <= now,
            or_(
                AppointmentWorkingSet.flow_state.is_(None),
                AppointmentWorkingSet.flow_state != NEXHEALTH_VISIT_COMPLETED,
            ),
        )
    )


async def _sweep_nexhealth_completed_visits_async() -> dict:
    from src.app.config import settings

    now = datetime.now(tz=timezone.utc)
    lookback_hours = int(settings.nexhealth_post_visit_lookback_hours)
    default_duration = int(settings.nexhealth_post_visit_default_duration_minutes)
    window_start = now - timedelta(hours=lookback_hours)

    completed: list[dict] = []

    # This is a trusted platform-wide maintenance sweep. Tenant contexts are
    # deliberately unable to enumerate another clinic's appointments.
    async with _superadmin_system_session("nexhealth_post_visit_sweep") as session:
        rows = (
            (await session.execute(completed_visit_candidates_query(window_start, now)))
            .unique()
            .all()
        )

        for appt, duration_minutes in rows:
            start = appt.start_time
            if start is None:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            ended_at = start + timedelta(
                minutes=int(duration_minutes or default_duration)
            )
            if ended_at > now or ended_at < window_start:
                continue

            appt.flow_state = NEXHEALTH_VISIT_COMPLETED
            # The end of the visit, not sweep time: the post-op template waits a
            # fixed offset from flow_changed_at, and that must mean the same
            # thing on both PMSs.
            appt.flow_changed_at = ended_at
            appt.last_status_source = "nexhealth_post_visit_sweep"
            completed.append(
                {
                    "institution_id": str(appt.institution_id),
                    "appointment_id": str(appt.nexhealth_appointment_id),
                    "location_id": str(appt.location_id) if appt.location_id else None,
                    "contact_id": str(appt.contact_id) if appt.contact_id else None,
                    "flow_changed_at": ended_at.isoformat(),
                }
            )

        await session.commit()

    # Fire triggers only after the marks are durable, so a crash between the two
    # re-marks rather than double-enrolling.
    for item in completed:
        trigger_appointment_state_workflows.delay(
            institution_id=item["institution_id"],
            appointment_id=item["appointment_id"],
            contact_id=item["contact_id"],
            location_id=item["location_id"],
            flow_state=NEXHEALTH_VISIT_COMPLETED,
            flow_changed_at=item["flow_changed_at"],
            trigger_metadata={
                "event": "nexhealth_visit_completed",
                "pms_source": "nexhealth",
            },
        )

    logger.info(
        "sweep_nexhealth_completed_visits: completed=%d lookback_hours=%d",
        len(completed),
        lookback_hours,
    )
    return {"completed": len(completed), "lookback_hours": lookback_hours}


# ---------------------------------------------------------------------------
# GoTracker writeback sweeper — fallback for missed .complete/.failed webhooks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.sweep_gotracker_appointment_writebacks",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def sweep_gotracker_appointment_writebacks(self) -> dict:
    """Resolve stale GoTracker appointment writes by reading live PMS state.

    The webhook path is the fast path. This task is the floor: if a completion
    webhook is lost or GoTracker's one pending slot overwrote an earlier write,
    stale pending rows do not stay pending forever.
    """
    _ensure_db()
    try:
        return asyncio.run(_sweep_gotracker_writebacks_async())
    except Exception as exc:
        logger.exception("sweep_gotracker_appointment_writebacks failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


async def _sweep_gotracker_writebacks_async() -> dict:
    from src.app.services.automation.gotracker_writeback_service import (
        GoTrackerAppointmentWritebackService,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=_GOTRACKER_WRITEBACK_STALE_SECONDS
    )
    async with _superadmin_system_session("gotracker_writeback_sweep") as session:
        rows = await GoTrackerAppointmentWritebackService(session).list_stale_pending(
            cutoff=cutoff,
            limit=_GOTRACKER_WRITEBACK_SWEEP_BATCH,
        )
        targets = [
            {
                "id": str(row.id),
                "institution_id": str(row.institution_id),
                "location_id": str(row.location_id) if row.location_id else None,
                "appointment_id": str(row.appointment_id),
            }
            for row in rows
        ]
        await session.commit()

    resolved = 0
    failed = 0
    skipped = 0
    errored = 0
    for target in targets:
        try:
            result = await _resolve_gotracker_writeback_target(**target)
        except Exception as exc:  # noqa: BLE001 - one bad location must not stop sweep
            errored += 1
            logger.warning(
                "gotracker_writeback_sweep: target errored institution=%s appointment=%s writeback=%s error=%s",
                target["institution_id"],
                target["appointment_id"],
                target["id"],
                exc,
                exc_info=True,
            )
            continue
        if result["status"] == "completed":
            resolved += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    logger.info(
        "gotracker_writeback_sweep: checked=%d completed=%d failed=%d skipped=%d errored=%d",
        len(targets),
        resolved,
        failed,
        skipped,
        errored,
    )
    return {
        "checked": len(targets),
        "completed": resolved,
        "failed": failed,
        "skipped": skipped,
        "errored": errored,
    }


async def _resolve_gotracker_writeback_target(
    *,
    id: str,
    institution_id: str,
    location_id: str | None,
    appointment_id: str,
) -> dict[str, str]:
    from src.app.api.routes.nexhealth_webhooks import _cancel_runs_for_appointment
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation
    from src.app.pms.factory import get_adapter_for_institution_location
    from src.app.services.automation.gotracker_writeback_service import (
        GoTrackerAppointmentWritebackService,
    )
    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )

    if not location_id:
        async with get_system_db_session(
            "celery",
            institution_id=institution_id,
            external_id=appointment_id,
        ) as session:
            writebacks = GoTrackerAppointmentWritebackService(session)
            row = await writebacks.get_pending(writeback_id=id)
            if row is None:
                return {"status": "skipped", "reason": "already_resolved"}
            await writebacks.fail(
                row, error="Missing location for GoTracker writeback sweep"
            )
            await session.commit()
        return {"status": "failed", "reason": "missing_location"}

    adapter = None
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=appointment_id,
    ) as session:
        writebacks = GoTrackerAppointmentWritebackService(session)
        row = await writebacks.get_pending(writeback_id=id)
        if row is None:
            await session.commit()
            return {"status": "skipped", "reason": "already_resolved"}

        institution = await session.get(Institution, institution_id)
        location = await session.get(InstitutionLocation, location_id)
        if institution is None or location is None:
            await writebacks.fail(
                row,
                error="Missing institution/location for GoTracker writeback sweep",
            )
            await session.commit()
            return {"status": "failed", "reason": "missing_scope"}

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            if not hasattr(adapter, "get_appointment"):
                await writebacks.fail(
                    row,
                    error="GoTracker adapter cannot fetch appointment for writeback sweep",
                )
                await session.commit()
                return {"status": "failed", "reason": "adapter_missing_get_appointment"}

            appointment = await adapter.get_appointment(appointment_id)  # type: ignore[attr-defined]
        finally:
            if adapter is not None:
                await adapter.close()

        current_start = _gotracker_sweep_start_time(appointment)
        current_status_id = _gotracker_sweep_status_id(appointment)
        current_cancelled = _gotracker_sweep_is_cancelled(
            appointment, current_status_id
        )
        applied = _gotracker_pending_matches_current_state(
            row,
            current_start=current_start,
            current_status_id=current_status_id,
            current_cancelled=current_cancelled,
            appointment=appointment,
        )

        projection = NexHealthProjectionService(session)
        if applied:
            await writebacks.complete(row, source_event_id=f"sweeper:{id}")
            if row.action == "reschedule" and row.requested_start_time is not None:
                appointment_at_iso = row.requested_start_time.isoformat()
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=row.contact_id,
                    start_time=appointment_at_iso,
                    event="appointment.status_writeback.swept",
                    cancelled=False,
                    provider_id=row.provider_id,
                    gotracker_status_id=row.status_id,
                    is_confirmed=row.confirmed,
                    is_preconfirmed=row.preconfirmed,
                    status_source="writeback_sweeper",
                )
                should_cancel_runs = True
                should_trigger_appointment = True
                should_trigger_state = False
            elif row.action == "cancel":
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=row.contact_id,
                    start_time=(
                        row.previous_start_time.isoformat()
                        if row.previous_start_time is not None
                        else current_start.isoformat()
                        if current_start is not None
                        else None
                    ),
                    event="appointment.status_writeback.swept",
                    cancelled=True,
                    gotracker_status_id=row.status_id,
                    is_confirmed=row.confirmed,
                    is_preconfirmed=row.preconfirmed,
                    status_source="writeback_sweeper",
                )
                should_cancel_runs = True
                should_trigger_appointment = False
                should_trigger_state = False
                appointment_at_iso = None
            else:
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=row.contact_id,
                    start_time=(
                        current_start.isoformat()
                        if current_start is not None
                        else row.previous_start_time.isoformat()
                        if row.previous_start_time is not None
                        else None
                    ),
                    event="appointment.status_writeback.swept",
                    cancelled=False,
                    gotracker_status_id=row.status_id,
                    is_confirmed=row.confirmed,
                    is_preconfirmed=row.preconfirmed,
                    status_source="writeback_sweeper",
                )
                should_cancel_runs = False
                should_trigger_appointment = False
                should_trigger_state = (
                    row.status_id is not None
                    or isinstance(row.confirmed, bool)
                    or isinstance(row.preconfirmed, bool)
                )
                appointment_at_iso = None
        else:
            await writebacks.fail(
                row,
                source_event_id=f"sweeper:{id}",
                error="GoTracker writeback did not apply within stale window",
            )
            if row.previous_start_time is not None:
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=row.contact_id,
                    start_time=row.previous_start_time.isoformat(),
                    event="appointment.status_writeback.swept_failed",
                    cancelled=False,
                    provider_id=row.provider_id,
                    status_source="writeback_sweeper_failed_restore",
                )
            should_cancel_runs = False
            should_trigger_appointment = False
            should_trigger_state = False
            appointment_at_iso = None

        await session.commit()

    if not applied:
        return {"status": "failed", "reason": "not_applied"}

    if should_cancel_runs:
        await _cancel_runs_for_appointment(
            institution_id,
            appointment_id,
            reason=f"gotracker_writeback_sweeper_{row.action}",
            include_running=False,
        )

    if should_trigger_appointment and appointment_at_iso is not None:
        trigger_appointment_workflows.delay(
            institution_id=institution_id,
            appointment_id=appointment_id,
            appointment_at_iso=appointment_at_iso,
            contact_id=row.contact_id,
            location_id=location_id,
            trigger_metadata={
                "event": "appointment.status_writeback.swept",
                "source": "gotracker_writeback_sweeper",
                "gotracker_appointment_id": appointment_id.removeprefix("gt-"),
                "appointment_at": appointment_at_iso,
                "appointment_datetime": appointment_at_iso,
                "origin_workflow_run_id": row.workflow_run_id,
            },
        )

    if should_trigger_state:
        trigger_appointment_state_workflows.delay(
            institution_id=institution_id,
            appointment_id=appointment_id,
            contact_id=row.contact_id,
            location_id=location_id,
            status_id=row.status_id,
            confirmed=row.confirmed,
            preconfirmed=row.preconfirmed,
            trigger_metadata={
                "event": "appointment.status_writeback.swept",
                "source": "gotracker_writeback_sweeper",
                "gotracker_appointment_id": appointment_id.removeprefix("gt-"),
            },
        )

    return {"status": "completed", "reason": row.action}


def _gotracker_sweep_start_time(appointment: dict[str, Any] | None) -> datetime | None:
    if not appointment:
        return None
    direct = _clean_sweep_str(
        _first_sweep(
            appointment,
            "start_time",
            "StartTime",
            "appointment_datetime",
            "AppointmentDateTime",
            "AppointmentTimeStamp",
        )
    )
    if direct:
        return _parse_sweep_dt(direct)

    appointment_date = _clean_sweep_str(
        _first_sweep(appointment, "appointment_date", "AppointmentDate", "date", "Date")
    )
    appointment_time = _clean_sweep_str(
        _first_sweep(appointment, "appointment_time", "AppointmentTime", "time", "Time")
    )
    if not appointment_date or not appointment_time:
        return None

    date_part = appointment_date.split("T", 1)[0]
    time_part = appointment_time.split("T", 1)[-1].removesuffix("Z")
    return _parse_sweep_dt(f"{date_part}T{time_part}Z")


def _gotracker_sweep_status_id(appointment: dict[str, Any] | None) -> int | None:
    if not appointment:
        return None
    raw = _first_sweep(appointment, "status_id", "StatusId", "statusId")
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _gotracker_sweep_is_cancelled(
    appointment: dict[str, Any] | None,
    status_id: int | None,
) -> bool:
    if not appointment:
        return False
    return is_non_attending_status(status_id) or _as_sweep_bool(
        _first_sweep(appointment, "cancelled", "Cancelled", "is_cancelled")
    )


def _gotracker_pending_matches_current_state(
    row,
    *,
    current_start: datetime | None,
    current_status_id: int | None,
    current_cancelled: bool,
    appointment: dict[str, Any] | None,
) -> bool:
    if row.action == "reschedule":
        return _same_sweep_instant(row.requested_start_time, current_start)
    if row.action == "cancel":
        return current_cancelled
    if row.status_id is not None and row.status_id != current_status_id:
        return False
    if isinstance(row.confirmed, bool):
        current_confirmed = _as_sweep_bool_or_none(
            _first_sweep(appointment or {}, "confirmed", "Confirmed", "is_confirmed")
        )
        if current_confirmed is None or row.confirmed != current_confirmed:
            return False
    if isinstance(row.preconfirmed, bool):
        current_preconfirmed = _as_sweep_bool_or_none(
            _first_sweep(
                appointment or {}, "preconfirmed", "Preconfirmed", "is_preconfirmed"
            )
        )
        if current_preconfirmed is None or row.preconfirmed != current_preconfirmed:
            return False
    return (
        row.status_id is not None
        or isinstance(row.confirmed, bool)
        or isinstance(row.preconfirmed, bool)
    )


def _first_sweep(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _clean_sweep_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_sweep_dt(value: Any) -> datetime | None:
    text_value = _clean_sweep_str(value)
    if not text_value:
        return None
    try:
        dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _same_sweep_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs((a - b).total_seconds()) < 1.0


def _as_sweep_bool(value: Any) -> bool:
    parsed = _as_sweep_bool_or_none(value)
    return bool(parsed) if parsed is not None else False


def _as_sweep_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
    return None


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
            workflow_id,
            contact_id,
            exc,
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
    retell_seed: tuple[str, str] | None = None
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
                "enroll_and_start: duplicate idempotency_key=%s — skipping",
                idempotency_key,
            )
            await session.commit()
            return {"run_id": str(run.id), "created": False}

        version = await session.get(AutomationWorkflowVersion, workflow_version_id)
        if version is None:
            logger.error(
                "enroll_and_start: version %s not found for workflow %s",
                workflow_version_id,
                workflow_id,
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
            run,
            definition,
            context=trigger_metadata,
            location_timezone=location_timezone,
        )
        current_node = next(
            (node for node in definition.nodes if node.id == run.current_step_id),
            None,
        )
        inbound_sms_message_id = trigger_metadata.get("inbound_sms_message_id")
        if (
            result.status == "waiting"
            and isinstance(current_node, RetellSmsConversationNode)
            and isinstance(inbound_sms_message_id, str)
            and inbound_sms_message_id
        ):
            from sqlalchemy import select

            from src.app.models.retell_sms import RetellSmsSession

            retell_session_id = (
                await session.execute(
                    select(RetellSmsSession.id).where(
                        RetellSmsSession.workflow_run_id == str(run.id),
                        RetellSmsSession.step_id == current_node.id,
                    )
                )
            ).scalar_one_or_none()
            if retell_session_id is not None:
                retell_seed = (str(retell_session_id), inbound_sms_message_id)
        await session.commit()

    if retell_seed is not None and location_id is not None:
        from src.app.tasks.retell_sms import process_retell_sms_turn

        process_retell_sms_turn.delay(
            institution_id=institution_id,
            location_id=location_id,
            session_id=retell_seed[0],
            inbound_sms_message_id=retell_seed[1],
        )

    _enqueue_patient_status_triggers(
        institution_id=institution_id,
        status_event_ids=result.patient_status_event_ids,
    )
    logger.info(
        "enroll_and_start: workflow=%s run=%s status=%s steps=%d",
        workflow_id,
        run.id,
        result.status,
        result.steps_advanced,
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
            institution_id,
            appointment_id,
            exc,
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
        appointment_context = await svc.get_appointment_context(
            institution_id=institution_id,
            appointment_id=appointment_id,
            fallback_location_id=location_id,
        )
        # Resolve the location first: a workflow bound to another location in the
        # same institution must not enroll for this appointment.
        workflows = await svc.find_active_appointment_workflows(
            institution_id,
            location_id=location_id or appointment_context.get("location_id"),
        )

    scheduled = 0
    skipped = 0
    skipped_type = 0
    skipped_filter = 0
    enriched_metadata = {
        **trigger_metadata,
        **{k: v for k, v in appointment_context.items() if v is not None},
        "appointment_id": appointment_id,
        "appointment_at": appointment_at_iso,
    }
    effective_contact_id = contact_id or appointment_context.get("contact_id")
    effective_location_id = location_id or appointment_context.get("location_id")
    for wf in workflows:
        if not wf.current_version_id:
            continue
        if not workflow_matches_appointment(
            wf,
            appointment_type_id=enriched_metadata.get("appointment_type_id"),
            appointment_type_name=enriched_metadata.get("appointment_type_name")
            or enriched_metadata.get("appointment_type"),
        ):
            skipped_type += 1
            logger.info(
                "trigger_appointment: skipping type mismatch appt=%s workflow=%s appointment_type_id=%s",
                appointment_id,
                wf.id,
                enriched_metadata.get("appointment_type_id"),
            )
            continue
        if not trigger_filter_matches(wf, enriched_metadata):
            skipped_filter += 1
            logger.info(
                "trigger_appointment: filtered out appt=%s workflow=%s",
                appointment_id,
                wf.id,
            )
            continue
        eta = compute_enrollment_eta(wf, appointment_at)
        if eta is None:
            skipped += 1
            logger.info(
                "trigger_appointment: skipping past-window appt=%s workflow=%s",
                appointment_id,
                wf.id,
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
                "contact_id": effective_contact_id,
                "location_id": effective_location_id,
                "trigger_type": "appointment_offset",
                "trigger_ref_type": "appointment",
                "trigger_ref_id": appointment_id,
                "idempotency_key": idempotency_key,
                "trigger_metadata": enriched_metadata,
            },
            eta=eta,
            queue="workflow",
        )
        scheduled += 1

    logger.info(
        "trigger_appointment: institution=%s appt=%s scheduled=%d skipped=%d "
        "skipped_type=%d skipped_filter=%d",
        institution_id,
        appointment_id,
        scheduled,
        skipped,
        skipped_type,
        skipped_filter,
    )
    return {
        "appointment_id": appointment_id,
        "scheduled": scheduled,
        "skipped": skipped,
        "skipped_type": skipped_type,
        # Counted separately so the enrollment reduction from moving eligibility
        # out of the graph is measurable rather than inferred.
        "skipped_filter": skipped_filter,
    }


@celery_app.task(
    name="src.app.tasks.automation_workflow.trigger_appointment_state_workflows",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def trigger_appointment_state_workflows(
    self,
    *,
    institution_id: str,
    appointment_id: str,
    contact_id: str | None = None,
    location_id: str | None = None,
    status_id: int | None = None,
    confirmed: bool | None = None,
    preconfirmed: bool | None = None,
    flow_state: str | None = None,
    flow_changed_at: str | None = None,
    trigger_metadata: dict | None = None,
) -> dict:
    """Enroll workflows that trigger from cached GoTracker appointment state."""
    _ensure_db()
    try:
        return asyncio.run(
            _trigger_appointment_state_async(
                institution_id=institution_id,
                appointment_id=appointment_id,
                contact_id=contact_id,
                location_id=location_id,
                status_id=status_id,
                confirmed=confirmed,
                preconfirmed=preconfirmed,
                flow_state=flow_state,
                flow_changed_at=flow_changed_at,
                trigger_metadata=trigger_metadata or {},
            )
        )
    except Exception as exc:
        logger.exception(
            "trigger_appointment_state_workflows failed: institution=%s appt=%s: %s",
            institution_id,
            appointment_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _trigger_appointment_state_async(
    *,
    institution_id: str,
    appointment_id: str,
    contact_id: str | None,
    location_id: str | None,
    status_id: int | None,
    confirmed: bool | None,
    preconfirmed: bool | None,
    flow_state: str | None = None,
    flow_changed_at: str | None = None,
    trigger_metadata: dict | None = None,
) -> dict:
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"appt_state_trigger:{appointment_id}",
    ) as session:
        svc = AppointmentTriggerService(session)
        appointment_context = await svc.get_appointment_context(
            institution_id=institution_id,
            appointment_id=appointment_id,
            fallback_location_id=location_id,
        )
        # Resolve the location first: a workflow bound to another location in the
        # same institution must not enroll for this appointment.
        workflows = await svc.find_active_appointment_state_workflows(
            institution_id,
            location_id=location_id or appointment_context.get("location_id"),
        )

    enriched_metadata = {
        **(trigger_metadata or {}),
        **{k: v for k, v in appointment_context.items() if v is not None},
        "appointment_id": appointment_id,
        "appointment_status_id": status_id,
        "gotracker_status_id": status_id,
        "is_confirmed": confirmed,
        "is_preconfirmed": preconfirmed,
        "appointment_flow_state": flow_state,
        "flow_state": flow_state,
        "appointment_flow_changed_at": flow_changed_at,
        "flow_changed_at": flow_changed_at,
    }
    effective_contact_id = contact_id or appointment_context.get("contact_id")
    effective_location_id = location_id or appointment_context.get("location_id")
    scheduled = 0
    skipped = 0
    for wf in workflows:
        if not wf.current_version_id:
            continue
        if not workflow_matches_appointment_state(
            wf,
            status_id=status_id,
            confirmed=confirmed,
            preconfirmed=preconfirmed,
            flow_state=flow_state,
        ):
            skipped += 1
            continue
        if not trigger_filter_matches(wf, enriched_metadata):
            skipped += 1
            logger.info(
                "trigger_appointment_state: filtered out appt=%s workflow=%s",
                appointment_id,
                wf.id,
            )
            continue
        idempotency_key = make_appointment_state_idempotency_key(
            str(wf.current_version_id),
            appointment_id,
            status_id=status_id,
            confirmed=confirmed,
            preconfirmed=preconfirmed,
            flow_state=flow_state,
            flow_changed_at=flow_changed_at,
        )
        workflow_metadata = dict(enriched_metadata)
        try:
            trigger = WorkflowDefinition.model_validate(wf.definition).trigger
            campaign_goal = getattr(trigger, "campaign_goal", None)
            if campaign_goal:
                workflow_metadata["campaign_goal"] = campaign_goal
            max_followup_delay_hours = getattr(
                trigger, "max_followup_delay_hours", None
            )
            if max_followup_delay_hours is not None and flow_changed_at:
                try:
                    flow_changed = datetime.fromisoformat(
                        flow_changed_at.replace("Z", "+00:00")
                    )
                    if flow_changed.tzinfo is None:
                        flow_changed = flow_changed.replace(tzinfo=timezone.utc)
                    workflow_metadata["post_op_expires_at"] = (
                        flow_changed + timedelta(hours=max_followup_delay_hours)
                    ).isoformat()
                except ValueError:
                    logger.warning(
                        "trigger_appointment_state: invalid flow_changed_at=%s",
                        flow_changed_at,
                    )
        except Exception:
            pass
        enroll_and_start_workflow_run.apply_async(
            kwargs={
                "institution_id": institution_id,
                "workflow_id": str(wf.id),
                "workflow_version_id": str(wf.current_version_id),
                "contact_id": effective_contact_id,
                "location_id": effective_location_id,
                "trigger_type": "appointment_state_changed",
                "trigger_ref_type": "appointment",
                "trigger_ref_id": appointment_id,
                "idempotency_key": idempotency_key,
                "trigger_metadata": workflow_metadata,
            },
            queue="workflow",
        )
        scheduled += 1

    logger.info(
        "trigger_appointment_state: institution=%s appt=%s scheduled=%d skipped=%d",
        institution_id,
        appointment_id,
        scheduled,
        skipped,
    )
    return {
        "appointment_id": appointment_id,
        "scheduled": scheduled,
        "skipped": skipped,
    }


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

    async with _superadmin_system_session(
        "nexhealth_subscription_lifecycle"
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
    name="src.app.tasks.automation_workflow.ensure_nexhealth_shadow_webhook_subscriptions",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def ensure_nexhealth_shadow_webhook_subscriptions(self) -> dict:
    """Ensure v3 shadow subscription rows and optionally create provider subscriptions.

    This task is intentionally not scheduled in Celery beat. Operators run it
    explicitly during NexHealth v3 webhook validation.
    """
    _ensure_db()
    try:
        return asyncio.run(_ensure_nexhealth_shadow_webhook_subscriptions_async())
    except Exception as exc:
        logger.exception(
            "ensure_nexhealth_shadow_webhook_subscriptions failed: %s", exc
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _ensure_nexhealth_shadow_webhook_subscriptions_async() -> dict:
    from src.app.config import settings

    async with _superadmin_system_session(
        "nexhealth_shadow_subscription_lifecycle"
    ) as session:
        svc = NexHealthWebhookShadowSubscriptionService(session)
        ensure_summary = await svc.ensure_for_configured_locations(
            callback_base_url=settings.nexhealth_shadow_webhook_callback_base_url,
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
    }


@celery_app.task(
    name="src.app.tasks.automation_workflow.ensure_gotracker_webhook_subscriptions",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def ensure_gotracker_webhook_subscriptions(self) -> dict:
    """Ensure GoTracker Synchronizer webhook subscriptions and refresh health."""
    _ensure_db()
    try:
        return asyncio.run(_ensure_gotracker_webhook_subscriptions_async())
    except Exception as exc:
        logger.exception("ensure_gotracker_webhook_subscriptions failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _ensure_gotracker_webhook_subscriptions_async() -> dict:
    from src.app.config import settings

    async with _superadmin_system_session(
        "gotracker_subscription_lifecycle"
    ) as session:
        svc = GoTrackerSubscriptionLifecycleService(session)
        ensure_summary = await svc.ensure_for_configured_locations(
            callback_base_url=settings.gotracker_webhook_callback_base_url,
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
    async with _superadmin_system_session("nexhealth_sync_status_poll") as session:
        summary = await NexHealthSyncStatusService(
            session
        ).poll_all_configured_locations()
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
        targets = await NexHealthSubscriptionLifecycleService(
            session
        ).active_or_pending_targets()

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
        targets = await NexHealthSubscriptionLifecycleService(
            session
        ).active_or_pending_targets()

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
            institution_id,
            call_id,
            exc,
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
                institution_id,
                call_id,
            )
            return {
                "call_id": call_id,
                "scheduled": 0,
                "skipped": "resolved_or_missing",
            }

        svc = CallbackTriggerService(session)
        workflows = await svc.find_active_callback_workflows(
            institution_id,
            location_id=location_id
            or (str(call.location_id) if call.location_id else None),
        )

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
                if not await comp.has_consent_record(
                    institution_id, phone, ConsentChannel.VOICE
                ):
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
        idempotency_key = make_callback_idempotency_key(
            str(wf.current_version_id), call_id
        )
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
        institution_id,
        call_id,
        scheduled,
        eta.isoformat() if eta else "now",
    )
    return {"call_id": call_id, "scheduled": scheduled}


# ---------------------------------------------------------------------------
# Patient status trigger — independent workflow handoff
# ---------------------------------------------------------------------------


def _enqueue_patient_status_triggers(
    *,
    institution_id: str,
    status_event_ids: list[str],
) -> None:
    for status_event_id in status_event_ids:
        trigger_patient_status_workflows.apply_async(
            kwargs={
                "institution_id": institution_id,
                "status_event_id": status_event_id,
            },
            queue="workflow",
        )


@celery_app.task(
    name="src.app.tasks.automation_workflow.trigger_patient_status_workflows",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def trigger_patient_status_workflows(
    self,
    *,
    institution_id: str,
    status_event_id: str,
) -> dict:
    """Enroll workflows that listen for a recorded patient workflow status."""
    _ensure_db()
    try:
        return asyncio.run(
            _trigger_patient_status_async(
                institution_id=institution_id,
                status_event_id=status_event_id,
            )
        )
    except Exception as exc:
        logger.exception(
            "trigger_patient_status_workflows failed: institution=%s event=%s: %s",
            institution_id,
            status_event_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _trigger_patient_status_async(
    *,
    institution_id: str,
    status_event_id: str,
) -> dict:
    from src.app.models.automation_workflow import AutomationWorkflowRun
    from src.app.models.patient_workflow_status import PatientWorkflowStatusEvent
    from src.app.services.automation.definition_schema import (
        PatientStatusChangedTrigger,
    )

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=f"patient_status_trigger:{status_event_id}",
    ) as session:
        event = await session.get(PatientWorkflowStatusEvent, status_event_id)
        if event is None or event.institution_id != institution_id:
            return {
                "status_event_id": status_event_id,
                "scheduled": 0,
                "reason": "event_not_found",
            }

        source_run = await session.get(AutomationWorkflowRun, event.workflow_run_id)
        source_metadata = dict(source_run.trigger_metadata or {}) if source_run else {}
        event_data = {
            "id": str(event.id),
            "status": event.status,
            "contact_id": event.contact_id,
            "location_id": event.location_id,
            "workflow_id": str(event.workflow_id),
            "workflow_run_id": str(event.workflow_run_id),
            "step_id": event.step_id,
        }

        svc = PatientStatusTriggerService(session)
        workflows = [
            (wf.id, wf.current_version_id, wf.definition)
            for wf in await svc.find_active_status_workflows(
                institution_id,
                # The status event carries the location of the run that recorded it.
                location_id=event_data["location_id"],
            )
        ]

    scheduled = 0
    skipped = 0
    base_trigger_metadata = {
        **source_metadata,
        "patient_workflow_status": event_data["status"],
        "patient_status": event_data["status"],
        "source_patient_status_event_id": event_data["id"],
        "source_workflow_id": event_data["workflow_id"],
        "source_workflow_run_id": event_data["workflow_run_id"],
        "source_workflow_step_id": event_data["step_id"],
    }
    for workflow_id, workflow_version_id, workflow_definition in workflows:
        if not workflow_version_id:
            continue
        if str(workflow_id) == event_data["workflow_id"]:
            skipped += 1
            continue
        try:
            definition = WorkflowDefinition.model_validate(workflow_definition)
        except Exception:
            skipped += 1
            continue
        trigger_metadata = base_trigger_metadata
        trigger = definition.trigger
        if not isinstance(trigger, PatientStatusChangedTrigger):
            skipped += 1
            continue
        if event_data["status"] not in trigger.statuses:
            skipped += 1
            continue
        if trigger.campaign_goal:
            trigger_metadata = {
                **trigger_metadata,
                "campaign_goal": trigger.campaign_goal,
            }

        idempotency_key = patient_status_idempotency_key(
            str(workflow_version_id),
            event_data["id"],
        )
        enroll_and_start_workflow_run.apply_async(
            kwargs={
                "institution_id": institution_id,
                "workflow_id": str(workflow_id),
                "workflow_version_id": str(workflow_version_id),
                "contact_id": event_data["contact_id"],
                "location_id": event_data["location_id"],
                "trigger_type": "patient_status_changed",
                "trigger_ref_type": "patient_workflow_status_event",
                "trigger_ref_id": event_data["id"],
                "idempotency_key": idempotency_key,
                "trigger_metadata": trigger_metadata,
            },
            queue="workflow",
        )
        scheduled += 1

    logger.info(
        "trigger_patient_status: institution=%s event=%s status=%s scheduled=%d skipped=%d",
        institution_id,
        status_event_id,
        event_data["status"],
        scheduled,
        skipped,
    )
    return {
        "status_event_id": status_event_id,
        "status": event_data["status"],
        "scheduled": scheduled,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Voice outcome resume — AI Voice outcome-feedback loop (Plan 03)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.app.tasks.automation_workflow.poll_retell_voice_outcomes",
    bind=True,
    max_retries=3,
    queue="workflow",
)
def poll_retell_voice_outcomes(self) -> dict:
    """Repair missed/delayed Retell final webhooks for parked voice steps.

    Retell can show a completed call in its dashboard while our app only received
    ``call_started``. This poller asks Retell for awaiting campaign calls and
    enqueues the normal ``resume_voice_outcome`` task once a terminal outcome is
    visible, so the workflow run does not stay elapsed forever.
    """
    _ensure_db()
    try:
        return asyncio.run(_poll_retell_voice_outcomes_async())
    except Exception as exc:
        logger.exception("poll_retell_voice_outcomes failed: %s", exc)
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _poll_retell_voice_outcomes_async() -> dict:
    from sqlalchemy import select

    from src.app.config import settings
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt
    from src.app.retell.security import hash_for_logging
    from src.app.services.automation.retell_outbound_client import (
        RetellOutboundClient,
        RetellPermanentError,
        RetellTransientError,
    )

    if not settings.retell_api_secret:
        return {
            "scanned": 0,
            "enqueued": 0,
            "pending": 0,
            "failed": 0,
            "skipped": "missing_api_key",
        }

    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        seconds=_RETELL_OUTCOME_MIN_AGE_SECONDS
    )
    async with _superadmin_system_session("retell_voice_outcome_poll") as session:
        result = await session.execute(
            select(WorkflowVoiceAttempt)
            .where(
                WorkflowVoiceAttempt.status
                == VoiceAttemptStatus.AWAITING_OUTCOME.value,
                WorkflowVoiceAttempt.retell_call_id.is_not(None),
                WorkflowVoiceAttempt.created_at <= cutoff,
            )
            .order_by(WorkflowVoiceAttempt.created_at.asc())
            .limit(_RETELL_OUTCOME_POLL_BATCH)
        )
        attempts = [
            (attempt.institution_id, attempt.retell_call_id)
            for attempt in result.scalars().all()
        ]

    client = RetellOutboundClient(settings.retell_api_secret)
    enqueued = 0
    pending = 0
    failed = 0
    for institution_id, retell_call_id in attempts:
        if not retell_call_id:
            pending += 1
            continue
        try:
            details = await client.get_phone_call(retell_call_id)
        except RetellTransientError as exc:
            pending += 1
            logger.info(
                "retell outcome poll pending: call=%s error=%s",
                hash_for_logging(retell_call_id),
                exc,
            )
            continue
        except RetellPermanentError as exc:
            failed += 1
            logger.warning(
                "retell outcome poll failed: call=%s error=%s",
                hash_for_logging(retell_call_id),
                exc,
            )
            continue

        if not _retell_call_details_ready_for_resume(details):
            pending += 1
            continue

        call_outcome = _retell_call_details_outcome(details)
        resume_voice_outcome.apply_async(
            kwargs={
                "institution_id": institution_id,
                "retell_call_id": retell_call_id,
                "call_outcome": call_outcome,
                "disconnection_reason": details.disconnection_reason,
                "outcome_context": _retell_call_details_context(details),
            },
            queue="workflow",
        )
        enqueued += 1

    return {
        "scanned": len(attempts),
        "enqueued": enqueued,
        "pending": pending,
        "failed": failed,
    }


def _retell_call_details_ready_for_resume(details) -> bool:
    status = (details.call_status or "").lower()
    if status in _RETELL_TERMINAL_CALL_STATUSES:
        return True
    return bool(details.call_analysis or details.scrubbed_call_analysis)


def _retell_call_details_outcome(details) -> str:
    """Extract the business outcome from Retell get-call data."""
    from src.app.services.automation.voice_outcome import map_disconnection_reason

    analysis = details.call_analysis or details.scrubbed_call_analysis or {}
    if isinstance(analysis, dict):
        custom = analysis.get("custom_analysis_data") or {}
        outcome = custom.get("call_outcome") if isinstance(custom, dict) else None
        if isinstance(outcome, str) and outcome:
            return outcome
    return map_disconnection_reason(details.disconnection_reason, details.call_status)


def _retell_call_details_context(details) -> dict[str, str]:
    from src.app.services.automation.voice_outcome import (
        extract_workflow_outcome_context,
    )

    analysis = details.call_analysis or details.scrubbed_call_analysis or {}
    custom = analysis.get("custom_analysis_data") if isinstance(analysis, dict) else {}
    return extract_workflow_outcome_context(custom)


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
    outcome_context: dict[str, str] | None = None,
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
                outcome_context=outcome_context,
            )
        )
    except Exception as exc:
        logger.exception(
            "resume_voice_outcome failed: institution=%s call=%s: %s",
            institution_id,
            retell_call_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _resume_voice_outcome_async(
    *,
    institution_id: str,
    retell_call_id: str,
    call_outcome: str,
    disconnection_reason: str | None = None,
    outcome_context: dict[str, str] | None = None,
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
            (
                await session.execute(
                    select(AutomationWorkflowStepExecution).where(
                        AutomationWorkflowStepExecution.institution_id
                        == institution_id,
                        AutomationWorkflowStepExecution.status
                        == AutomationStepStatus.WAITING.value,
                        AutomationWorkflowStepExecution.result_code
                        == _CALL_PLACED_AWAITING,
                    )
                )
            )
            .scalars()
            .all()
        )
        step = next(
            (
                s
                for s in rows
                if (s.result_metadata or {}).get("retell_call_id") == retell_call_id
            ),
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
        from src.app.services.automation.voice_outcome import (
            extract_workflow_outcome_context,
        )

        md = dict(run.trigger_metadata or {})
        md["call_outcome"] = call_outcome
        md.update(extract_workflow_outcome_context(outcome_context or {}))
        run.trigger_metadata = md
        await session.flush()

        # Resolve the voice-attempt row (V-4) to COMPLETED with a dial-level
        # outcome. Retell custom analysis may provide business outcomes like
        # "confirmed"; those belong in run context / response events, not in the
        # constrained dial_outcome column.
        dial_outcome = _dial_outcome_for_attempt(
            call_outcome=call_outcome,
            disconnection_reason=disconnection_reason,
        )
        await stamp_attempt_outcome(
            session,
            institution_id=institution_id,
            retell_call_id=retell_call_id,
            dial_outcome=dial_outcome,
            disconnection_reason=disconnection_reason,
        )

        # The call is over, so give its concurrency slot back (Item 18). The
        # slot was re-labelled to the call id when the call was placed, which is
        # why the id is all this needs. Best-effort by design: if this never
        # runs — a dropped webhook, a crash here — the lease expires by itself
        # and the ceiling corrects without anyone noticing.
        try:
            await _outbound_limits().release_call_slot_by_token(
                institution_id, retell_call_id
            )
        except Exception:  # noqa: BLE001 — never fail an outcome on this
            logger.warning(
                "could not release the call slot for call=%s", retell_call_id
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

    _enqueue_patient_status_triggers(
        institution_id=institution_id,
        status_event_ids=result.patient_status_event_ids,
    )
    logger.info(
        "resume_voice_outcome: institution=%s call=%s outcome=%s status=%s",
        institution_id,
        retell_call_id,
        call_outcome,
        result.status,
    )
    return {"resumed": True, "status": result.status, "call_outcome": call_outcome}


def _dial_outcome_for_attempt(
    *, call_outcome: str, disconnection_reason: str | None
) -> str:
    """Return a DB-safe dial outcome for ``workflow_voice_attempts``."""
    from src.app.models.outbound_voice import VOICE_DIAL_OUTCOMES
    from src.app.services.automation.voice_outcome import map_disconnection_reason

    if call_outcome in VOICE_DIAL_OUTCOMES:
        return call_outcome

    mapped = map_disconnection_reason(disconnection_reason)
    if mapped in VOICE_DIAL_OUTCOMES:
        return mapped
    return "unknown"


# ---------------------------------------------------------------------------
# Context-field resume — SMS confirmations + reactivation booking detection
# ---------------------------------------------------------------------------


def _waiting_step_targets_field(
    definition: WorkflowDefinition, current_step_id: str | None, field: str
) -> bool:
    """True when the current wait-like node flows into a ConditionNode reading field."""
    if not current_step_id:
        return False
    from src.app.services.automation.definition_schema import WaitForSmsReplyNode

    node_map = {node.id: node for node in definition.nodes}
    current = node_map.get(current_step_id)
    if not isinstance(current, (WaitNode, WaitForSmsReplyNode)):
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
    workflow_run_id: str | None = None,
    conversation_thread_id: str | None = None,
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
                workflow_run_id=workflow_run_id,
                conversation_thread_id=conversation_thread_id,
            )
        )
    except Exception as exc:
        logger.exception(
            "resume_sms_confirmation failed: institution=%s location=%s: %s",
            institution_id,
            location_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))


async def _resume_sms_confirmation_async(
    *,
    institution_id: str,
    location_id: str,
    from_number: str,
    body: str,
    message_sid: str | None = None,
    workflow_run_id: str | None = None,
    conversation_thread_id: str | None = None,
) -> dict:
    from sqlalchemy import select

    from src.app.models.contact import Contact

    phone_hash = Contact.find_by_phone_hash(from_number)
    if not phone_hash:
        return {"resumed": 0, "reason": "phone_hash_unavailable"}
    if not workflow_run_id:
        return {"resumed": 0, "reason": "conversation_thread_required"}

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=f"sms_confirmation:{conversation_thread_id or workflow_run_id}",
    ) as session:
        contacts = (
            (
                await session.execute(
                    select(Contact.id).where(
                        Contact.institution_id == institution_id,
                        Contact.phone_hash == phone_hash,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not contacts:
            return {"resumed": 0, "reason": "contact_not_found"}

        mapped = await _resume_mapped_sms_response(
            session=session,
            institution_id=institution_id,
            location_id=location_id,
            contact_ids=[str(contact_id) for contact_id in contacts],
            workflow_run_id=workflow_run_id,
            body=body,
            message_sid=message_sid,
            conversation_thread_id=conversation_thread_id,
        )
        if mapped is not None:
            await session.commit()
            return mapped

        from src.app.services.automation.sms_intent_parser import parse_sms_intent

        if parse_sms_intent(body).intent != "confirm":
            await session.commit()
            return {"resumed": 0, "reason": "no_sms_response_mapping"}

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
                "sms_confirmation_conversation_thread_id": conversation_thread_id,
            },
            workflow_run_id=workflow_run_id,
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


async def _resume_mapped_sms_response(
    *,
    session,
    institution_id: str,
    location_id: str,
    contact_ids: list[str],
    workflow_run_id: str,
    body: str,
    message_sid: str | None,
    conversation_thread_id: str | None,
) -> dict | None:
    from sqlalchemy import select

    from src.app.models.campaign_response import CampaignResponseEvent
    from src.app.services.automation.campaign_conversation_service import (
        CampaignConversationService,
    )

    match = await CampaignConversationService(session).match_sms_response_mapping(
        workflow_run_id=workflow_run_id,
        body=body,
    )
    if match is None:
        return None

    mapping = match.mapping
    response_event_id = None
    if message_sid:
        event = (
            await session.execute(
                select(CampaignResponseEvent).where(
                    CampaignResponseEvent.institution_id == institution_id,
                    CampaignResponseEvent.channel == "sms",
                    CampaignResponseEvent.source_event_id == message_sid,
                )
            )
        ).scalar_one_or_none()
        response_event_id = str(event.id) if event is not None else None

    metadata_updates = {
        "sms_response_reply": body.strip(),
        "sms_response_message_sid": message_sid,
        "sms_response_conversation_thread_id": conversation_thread_id,
        "sms_response_node_id": match.node_id,
        "sms_response_mapping_tokens": mapping.tokens,
        "last_campaign_response_event_id": response_event_id,
    }

    if not mapping.context_updates:
        return {
            "resumed": 0,
            "matched": 1,
            "outcomes": {},
            "reason": "mapping_created_handoff"
            if mapping.handoff_reason
            else "mapping_no_context_updates",
        }

    result = await _resume_waiting_run_with_context_updates(
        session=session,
        institution_id=institution_id,
        location_id=location_id,
        contact_ids=contact_ids,
        workflow_run_id=workflow_run_id,
        context_updates=dict(mapping.context_updates),
        metadata_updates=metadata_updates,
    )
    return {
        "resumed": result["resumed"],
        "matched": result["matched"],
        "outcomes": result["outcomes"],
        "mapping_node_id": match.node_id,
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
            institution_id,
            location_id,
            contact_id,
            exc,
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


async def _resume_waiting_run_with_context_updates(
    *,
    session,
    institution_id: str,
    location_id: str,
    contact_ids: list[str],
    workflow_run_id: str,
    context_updates: dict,
    metadata_updates: dict,
) -> dict:
    from sqlalchemy import select

    if not contact_ids:
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    run = (
        await session.execute(
            select(AutomationWorkflowRun).where(
                AutomationWorkflowRun.id == workflow_run_id,
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.location_id == location_id,
                AutomationWorkflowRun.contact_id.in_(contact_ids),
                AutomationWorkflowRun.status == AutomationRunStatus.WAITING.value,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
    if version is None:
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    definition = WorkflowDefinition.model_validate(version.definition)
    target_fields = [field for field in context_updates if field]
    if target_fields and not any(
        _waiting_step_targets_field(definition, run.current_step_id, field)
        for field in target_fields
    ):
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    await AutomationWorkflowSchedulerService(session).cancel_timers_for_run(run.id)
    md = dict(run.trigger_metadata or {})
    md.update({k: v for k, v in metadata_updates.items() if v is not None})
    md.update(context_updates)
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
    outcomes: dict[str, int] = {}
    outcome_runs: dict[str, list[AutomationWorkflowRun]] = {}
    resumed = 0
    if result.status == "completed":
        resumed = 1
        if result.outcome:
            outcomes[result.outcome] = 1
            outcome_runs[result.outcome] = [run]
    return {
        "matched": 1,
        "resumed": resumed,
        "outcomes": outcomes,
        "outcome_runs": outcome_runs,
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
    workflow_run_id: str | None = None,
) -> dict:
    from sqlalchemy import select

    if not contact_ids:
        return {"matched": 0, "resumed": 0, "outcomes": {}, "outcome_runs": {}}

    stmt = select(AutomationWorkflowRun).where(
        AutomationWorkflowRun.institution_id == institution_id,
        AutomationWorkflowRun.location_id == location_id,
        AutomationWorkflowRun.contact_id.in_(contact_ids),
        AutomationWorkflowRun.status == AutomationRunStatus.WAITING.value,
    )
    if workflow_run_id:
        stmt = stmt.where(AutomationWorkflowRun.id == workflow_run_id)
    rows = (await session.execute(stmt)).scalars().all()

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
        if not _waiting_step_targets_field(
            definition, run.current_step_id, context_field
        ):
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
    from src.app.pms.factory import get_adapter_for_institution_location
    from src.app.services.automation.pms_capability_service import PmsCapabilityService
    from src.app.services.audit import log_audit
    from src.app.services.sms_privacy import safe_error_summary

    institution = await session.get(Institution, institution_id)
    location = await session.get(InstitutionLocation, location_id)
    if institution is None or location is None:
        return

    if getattr(institution, "pms_type", None) != "gotracker":
        capability = await PmsCapabilityService(session).evaluate_location(
            institution=institution,
            location=location,
            requirements=["confirmation_writeback"],
        )
        if not capability.supported:
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
                        "pms_capability_evaluation": capability.as_dict(),
                    },
                    institution_id=institution_id,
                    location_id=location_id,
                )
            return

    adapter = None
    try:
        adapter = await get_adapter_for_institution_location(institution, location)
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
_RECALL_DEFAULT_COOLDOWN_DAYS = 90
_FUTURE_APPOINTMENT_STATUSES = ("scheduled", "booked", "booked_waiting", "pending")


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
    # NexHealth's field is `date_due` — verified against the v20240412 OpenAPI
    # spec and a live response. None of the other spellings exist, so before
    # this every record fell through to the missing-date branch below and was
    # treated as due: 8,862 recalls at one clinic, all "overdue".
    raw = (
        recall.get("date_due")
        or recall.get("due_date")
        or recall.get("due")
        or recall.get("next_visit_date")
    )
    if not raw:
        return True
    try:
        due = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= now


def _recall_cooldown_days(candidate: dict) -> int:
    workflow = candidate.get("workflow")
    if workflow is None or not getattr(workflow, "definition", None):
        return _RECALL_DEFAULT_COOLDOWN_DAYS
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return _RECALL_DEFAULT_COOLDOWN_DAYS
    if definition.trigger.type != "recall_scan":
        return _RECALL_DEFAULT_COOLDOWN_DAYS
    return int(
        getattr(
            definition.trigger,
            "recall_reenrollment_cooldown_days",
            _RECALL_DEFAULT_COOLDOWN_DAYS,
        )
        or _RECALL_DEFAULT_COOLDOWN_DAYS
    )


async def _has_recent_recall_enrollment(
    session: Any,
    *,
    institution_id: str,
    workflow_id: str,
    patient_id: str,
    now: datetime,
    cooldown_days: int,
) -> bool:
    from sqlalchemy import select as sa_select

    cutoff = now - timedelta(days=cooldown_days)
    result = await session.execute(
        sa_select(AutomationWorkflowRun.id)
        .where(
            AutomationWorkflowRun.institution_id == institution_id,
            AutomationWorkflowRun.workflow_id == workflow_id,
            AutomationWorkflowRun.trigger_type == "recall_scan",
            AutomationWorkflowRun.trigger_ref_type == "recall",
            AutomationWorkflowRun.trigger_ref_id == patient_id,
            AutomationWorkflowRun.created_at >= cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _patient_has_future_appointment(
    session: Any,
    *,
    institution_id: str,
    location_id: str,
    patient_id: str,
    contact_id: str | None,
    now: datetime,
) -> bool:
    from sqlalchemy import or_, select as sa_select

    from src.app.models.appointment_working_set import AppointmentWorkingSet

    patient_clauses = [AppointmentWorkingSet.nexhealth_patient_id == patient_id]
    if contact_id:
        patient_clauses.append(AppointmentWorkingSet.contact_id == contact_id)

    result = await session.execute(
        sa_select(AppointmentWorkingSet.id)
        .where(
            AppointmentWorkingSet.institution_id == institution_id,
            AppointmentWorkingSet.location_id == location_id,
            AppointmentWorkingSet.status.in_(_FUTURE_APPOINTMENT_STATUSES),
            AppointmentWorkingSet.start_time.is_not(None),
            AppointmentWorkingSet.start_time >= now,
            or_(*patient_clauses),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _scan_recall_async() -> dict:
    from sqlalchemy import select as sa_select

    from src.app.models.automation_workflow import (
        AutomationWorkflow,
        AutomationWorkflowStatus,
    )

    # Enumerate only workflow routing metadata globally; each institution is
    # processed below in its own tenant-scoped session.
    async with _superadmin_system_session("recall_scanner") as session:
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
                    # None means institution-wide; a value binds the workflow to
                    # one location and it must not enroll from another's recalls.
                    "location_id": (
                        str(wf.location_id) if wf.location_id is not None else None
                    ),
                    "workflow": wf,
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
        len(by_institution),
        active_workflows,
        total_enrolled,
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
        "celery",
        institution_id=institution_id,
        external_id=f"recall_scan:{institution_id}",
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
            location_workflows = [
                wf
                for wf in workflows
                if wf["location_id"] is None or wf["location_id"] == str(location.id)
            ]
            if not location_workflows:
                continue

            try:
                adapter = await NexHealthAdapter.create(institution, location)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recall_scan: adapter build failed inst=%s loc=%s: %s",
                    institution_id,
                    location.id,
                    exc,
                )
                continue
            try:
                recalls = await adapter.list_patient_recalls()
                recall_types = await _list_recall_types_safe(adapter)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recall_scan: recall pull failed inst=%s loc=%s: %s",
                    institution_id,
                    location.id,
                    exc,
                )
                await _close_adapter_safe(adapter)
                continue
            try:
                treatment_plan_cache: dict[str, list[Any] | None] = {}
                location_timezone = _location_timezone(location)

                for recall in recalls:
                    patient_id = _recall_patient_id(recall)
                    if not patient_id or not _recall_is_due(recall, now=now):
                        continue

                    adapter_source = _adapter_source(adapter)
                    recall_model = patient_recall_from_raw(
                        recall, source=adapter_source
                    )
                    base_metadata = {
                        "nexhealth_patient_id": patient_id,
                        "recall_due_date": recall.get("date_due")
                        or recall.get("due_date"),
                        "recall_period": period,
                    }

                    contact_row = await session.execute(
                        sa_select(Contact).where(
                            Contact.institution_id == institution_id,
                            Contact.nexhealth_patient_id == patient_id,
                        )
                    )
                    contact = contact_row.scalar_one_or_none()
                    contact_id = str(contact.id) if contact else None

                    if await _patient_has_future_appointment(
                        session,
                        institution_id=institution_id,
                        location_id=str(location.id),
                        patient_id=patient_id,
                        contact_id=contact_id,
                        now=now,
                    ):
                        continue

                    matched_workflows: list[tuple[dict, dict[str, Any]]] = []
                    for wf in location_workflows:
                        allowed_fields = _workflow_pms_context_fields(wf)
                        treatment_plans: list[Any] = []
                        if _needs_treatment_context(allowed_fields):
                            if patient_id not in treatment_plan_cache:
                                treatment_plan_cache[
                                    patient_id
                                ] = await _list_treatment_plans_safe(
                                    adapter,
                                    patient_id,
                                )
                            if treatment_plan_cache[patient_id] is None:
                                continue
                            treatment_plans = treatment_plan_cache[patient_id]

                        snapshot = PatientCommunicationSnapshot(
                            source=adapter_source,
                            patient_id=recall_model.patient_id,
                            fetched_at=now.isoformat(),
                            patient_recalls=[recall_model],
                            recall_types=recall_types,
                            treatment_plans=treatment_plans,
                            patient_alerts_included=False,
                            patient_alerts_policy="Excluded by Decision G.",
                        )
                        trigger_metadata = {
                            **base_metadata,
                            **patient_communication_workflow_context(
                                snapshot,
                                allowed_fields,
                            ),
                        }
                        if not workflow_matches_recall(
                            wf["workflow"],
                            trigger_metadata,
                            location_timezone=location_timezone,
                        ):
                            continue

                        cooldown_days = _recall_cooldown_days(wf)
                        if await _has_recent_recall_enrollment(
                            session,
                            institution_id=institution_id,
                            workflow_id=wf["workflow_id"],
                            patient_id=patient_id,
                            now=now,
                            cooldown_days=cooldown_days,
                        ):
                            continue

                        matched_workflows.append((wf, trigger_metadata))

                    if not matched_workflows:
                        continue

                    for wf, trigger_metadata in matched_workflows:
                        cooldown_days = _recall_cooldown_days(wf)
                        trigger_metadata = {
                            **trigger_metadata,
                            "recall_reenrollment_cooldown_days": cooldown_days,
                        }
                        key = make_recall_idempotency_key(
                            wf["version_id"], patient_id, period
                        )
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
                                "trigger_metadata": trigger_metadata,
                            },
                            queue="workflow",
                        )
                        enrolled += 1
            finally:
                await adapter.close()

    return enrolled


async def _list_recall_types_safe(adapter: Any) -> list[UniversalRecallType]:
    try:
        rows = await adapter.list_recall_types(max_items=500)
    except (AttributeError, NotImplementedError):
        return []
    except Exception as exc:  # noqa: BLE001 - recall rows remain usable without names.
        logger.warning(
            "recall_scan: recall type pull failed source=%s type=%s",
            _adapter_source(adapter),
            type(exc).__name__,
        )
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, UniversalRecallType)]


async def _list_treatment_plans_safe(adapter: Any, patient_id: str) -> list[Any] | None:
    try:
        rows = await adapter.list_treatment_plans(patient_id=patient_id, max_items=100)
    except (AttributeError, NotImplementedError):
        return None
    except Exception as exc:  # noqa: BLE001 - fail closed for treatment filters.
        logger.warning(
            "recall_scan: treatment plan pull failed source=%s patient=%s type=%s",
            _adapter_source(adapter),
            patient_id,
            type(exc).__name__,
        )
        return None
    return rows if isinstance(rows, list) else None


async def _close_adapter_safe(adapter: Any) -> None:
    try:
        await adapter.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recall_scan: adapter cleanup failed source=%s type=%s",
            _adapter_source(adapter),
            type(exc).__name__,
        )


def _workflow_pms_context_fields(candidate: dict) -> list[str]:
    workflow = candidate.get("workflow")
    if workflow is None or not getattr(workflow, "definition", None):
        return ["recall_due_date"]
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return ["recall_due_date"]

    fields = list(definition.pms_context_fields)
    # The recall scanner has always supplied the due date; keep that intrinsic
    # trigger field available while Item 25's extra PMS facts remain explicit.
    if definition.trigger.type == "recall_scan" and "recall_due_date" not in fields:
        fields.append("recall_due_date")
    return fields


def _needs_treatment_context(fields: list[str]) -> bool:
    return "treatment_plans" in pms_context_requirements(fields) or bool(
        set(fields) & TREATMENT_PLAN_CONTEXT_FIELDS
    )


def _location_timezone(location: Any) -> str:
    timezone_name = getattr(location, "timezone", None)
    if isinstance(timezone_name, str) and timezone_name.strip():
        return timezone_name.strip()
    return "UTC"


def _adapter_source(adapter: Any) -> str:
    source = getattr(adapter, "source", None)
    if isinstance(source, str) and source.strip():
        return source.strip()
    return "nexhealth"
