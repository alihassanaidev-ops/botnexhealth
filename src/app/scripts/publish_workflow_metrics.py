"""Publish automation workflow-engine health metrics to CloudWatch.

ECS CPU/memory scaling cannot see workflow-engine backpressure: a growing
due-timer backlog, timers stranded in CLAIMED by a crashed worker, or a spike
in failed runs/steps. This short-lived task runs on a schedule, counts those
conditions with a handful of SQL COUNTs, and emits one CloudWatch metric per
signal so alarms can fire on real workflow health.

Mirrors ``publish_queue_metrics``: reads the namespace from ``APP_NAME`` /
``APP_ENV`` env vars, publishes via ``boto3`` ``put_metric_data``, and exposes a
sync ``main()`` entrypoint (used by the ECS scheduled task and Celery beat).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from sqlalchemy import func, select

from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationTimerStatus,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationWorkflowTimer,
)

logger = logging.getLogger(__name__)

# Only count run failures from the recent past so a long-lived backlog of old
# failures doesn't keep an alarm permanently red.
_FAILED_RUN_WINDOW = timedelta(hours=24)


def _ensure_db() -> None:
    from src.app.config import settings

    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


async def collect_workflow_metrics() -> dict[str, int]:
    """Count workflow-engine health signals via grouped SQL COUNT queries."""
    now = datetime.now(tz=timezone.utc)
    # NOTE: The Celery DB role must have cross-institution visibility on the
    # automation_workflow_* tables (BYPASSRLS or a scheduler-specific policy),
    # otherwise these counts are silently scoped to one tenant.
    async with get_system_db_session("celery", external_id="workflow_metrics") as session:
        due_timer_backlog = (
            await session.execute(
                select(func.count())
                .select_from(AutomationWorkflowTimer)
                .where(
                    AutomationWorkflowTimer.status == AutomationTimerStatus.PENDING.value,
                    AutomationWorkflowTimer.due_at <= now,
                )
            )
        ).scalar_one()

        stale_timers = (
            await session.execute(
                select(func.count())
                .select_from(AutomationWorkflowTimer)
                .where(
                    AutomationWorkflowTimer.status == AutomationTimerStatus.CLAIMED.value,
                    AutomationWorkflowTimer.claim_expires_at <= now,
                )
            )
        ).scalar_one()

        active_runs = (
            await session.execute(
                select(func.count())
                .select_from(AutomationWorkflowRun)
                .where(
                    AutomationWorkflowRun.status.in_(
                        [
                            AutomationRunStatus.RUNNING.value,
                            AutomationRunStatus.WAITING.value,
                        ]
                    )
                )
            )
        ).scalar_one()

        failed_runs = (
            await session.execute(
                select(func.count())
                .select_from(AutomationWorkflowRun)
                .where(
                    AutomationWorkflowRun.status == AutomationRunStatus.FAILED.value,
                    AutomationWorkflowRun.created_at >= now - _FAILED_RUN_WINDOW,
                )
            )
        ).scalar_one()

        failed_steps = (
            await session.execute(
                select(func.count())
                .select_from(AutomationWorkflowStepExecution)
                .where(
                    AutomationWorkflowStepExecution.status == AutomationStepStatus.FAILED.value,
                )
            )
        ).scalar_one()

    return {
        "due_timer_backlog": int(due_timer_backlog or 0),
        "stale_timers": int(stale_timers or 0),
        "active_runs": int(active_runs or 0),
        "failed_runs": int(failed_runs or 0),
        "failed_steps": int(failed_steps or 0),
    }


async def publish_workflow_metrics() -> dict[str, int]:
    """Collect workflow-engine counts and publish them to CloudWatch."""
    _ensure_db()
    counts = await collect_workflow_metrics()

    app_name = os.getenv("APP_NAME", "nex-health")
    app_env = os.getenv("APP_ENV", "production")
    if app_env.lower() in {"local", "dev", "test"}:
        logger.info("Collected workflow metrics without CloudWatch publish: %s", counts)
        return counts

    namespace = f"{app_name}/{app_env}"

    metric_data = [
        {"MetricName": metric_name, "Unit": "Count", "Value": counts[key]}
        for metric_name, key in (
            ("WorkflowDueTimerBacklog", "due_timer_backlog"),
            ("WorkflowStaleTimers", "stale_timers"),
            ("WorkflowActiveRuns", "active_runs"),
            ("WorkflowFailedRuns", "failed_runs"),
            ("WorkflowFailedSteps", "failed_steps"),
        )
    ]

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    boto3.client("cloudwatch", region_name=region).put_metric_data(
        Namespace=namespace,
        MetricData=metric_data,
    )
    logger.info("Published workflow metrics: %s", counts)
    return counts


def main() -> int:
    asyncio.run(publish_workflow_metrics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
