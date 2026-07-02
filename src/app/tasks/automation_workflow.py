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
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
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
        dispatcher = WorkflowStepDispatcher(session, runtime, scheduler)

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
