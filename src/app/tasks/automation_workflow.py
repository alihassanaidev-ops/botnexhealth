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
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.revalidation import PmsLiveRevalidationService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.step_dispatcher import build_dispatcher
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
