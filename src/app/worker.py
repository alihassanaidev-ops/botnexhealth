"""Celery app configuration for background jobs."""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

from src.app.config import settings

logger = logging.getLogger(__name__)


def _build_celery_app() -> Celery:
    broker_url = settings.normalized_celery_broker_url or "redis://localhost:6379/0"

    app = Celery(
        "nex_health",
        broker=broker_url,
        include=[
            "src.app.tasks.notifications",
            "src.app.tasks.in_app_notifications",
            "src.app.tasks.sms",
            "src.app.tasks.recordings",
            "src.app.tasks.webhooks",
            "src.app.tasks.automation_workflow",
        ],
    )

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_default_queue="notifications_default",
        task_queues=(
            Queue("notifications_default"),
            Queue("notifications_high"),
            # Dedicated queue so a backlog of webhook-processing tasks
            # (e.g., during a Retell retry storm) doesn't starve the
            # notification/SMS queues for worker capacity.
            Queue("webhooks"),
            # Workflow engine scheduler and dispatch tasks.
            Queue("workflow"),
        ),
        # Per-task names use dotted prefixes (``webhooks.*``,
        # ``notifications.*``) so Celery routes them to the right
        # queue without each task having to specify ``queue=`` itself.
        task_routes={
            "webhooks.*": {"queue": "webhooks"},
            "src.app.tasks.automation_workflow.*": {"queue": "workflow"},
        },
        beat_schedule={
            "poll-workflow-timers": {
                "task": "src.app.tasks.automation_workflow.poll_workflow_timers",
                "schedule": 30.0,  # seconds
            },
            "scan-recall-workflows": {
                "task": "src.app.tasks.automation_workflow.scan_recall_workflows",
                "schedule": 3600.0,  # hourly — patient visit history changes slowly
            },
            "ensure-nexhealth-webhook-subscriptions": {
                "task": "src.app.tasks.automation_workflow.ensure_nexhealth_webhook_subscriptions",
                # Lifecycle/health is cheap and keeps setup state current.
                "schedule": 3600.0,
            },
            "reconcile-nexhealth-appointments": {
                "task": "src.app.tasks.automation_workflow.reconcile_nexhealth_appointments",
                # Low-frequency repair sweep; initial backfill is task-triggered
                # after subscription setup or manually by operators.
                "schedule": 6 * 3600.0,
            },
            "reconcile-nexhealth-patients": {
                "task": "src.app.tasks.automation_workflow.reconcile_nexhealth_patients",
                # Repairs missed patient webhooks and keeps contact hints fresh.
                "schedule": 6 * 3600.0,
            },
            "poll-nexhealth-sync-statuses": {
                "task": "src.app.tasks.automation_workflow.poll_nexhealth_sync_statuses",
                # Sync-status webhooks mainly signal recovery; polling catches
                # PMS read/write failures that do not emit a webhook.
                "schedule": 15 * 60.0,
            },
            "recover-stale-workflow-timers": {
                "task": "src.app.tasks.automation_workflow.recover_stale_workflow_timers",
                # Faster than the 120 s claim TTL so a crashed-worker timer is
                # returned to PENDING and re-dispatched within one TTL window.
                "schedule": 60.0,
            },
            "publish-workflow-metrics": {
                "task": "src.app.tasks.automation_workflow.publish_workflow_metrics",
                # Emit workflow-engine health metrics (backlog, stale timers,
                # active/failed runs, failed steps) to CloudWatch every minute.
                "schedule": 60.0,
            },
        },
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )

    return app


celery_app = _build_celery_app()

# Keep `app` alias so `celery -A src.app.worker worker ...` works.
app = celery_app


@worker_process_init.connect
def _init_database_in_worker_process(**_: object) -> None:
    """Initialize the SQLAlchemy async engine in each forked worker process.

    Why post-fork (``worker_process_init``) and not master (``worker_init``):
    initializing on the master would open a TCP socket that ``os.fork()`` then
    duplicates into every child, producing cross-process corruption. Each
    forked child must build its own engine.

    Why ``NullPool``: each Celery task runs inside its own ``asyncio.run()``
    event loop. Pooled asyncpg connections bind to the loop on which they
    were created, so the second task on a worker would crash with
    ``RuntimeError: ... attached to a different loop``. NullPool opens a
    fresh connection per checkout and closes it on checkin, so every task
    gets a connection bound to its own loop.
    """
    from src.app.database import init_database, is_database_initialized

    if is_database_initialized():
        return
    if not settings.database_url:
        logger.warning(
            "DATABASE_URL not set at worker process init; tasks will lazy-init."
        )
        return
    init_database(settings.database_url, use_null_pool=True)
    logger.info("Celery worker process: database engine initialized with NullPool")
