"""Celery app configuration for background jobs."""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from src.app.config import settings


def _build_celery_app() -> Celery:
    broker_url = settings.celery_broker_url or "redis://localhost:6379/0"

    app = Celery(
        "nex_health",
        broker=broker_url,
        include=[
            "src.app.tasks.notifications",
            "src.app.tasks.in_app_notifications",
            "src.app.tasks.sms",
            "src.app.tasks.recordings",
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
        ),
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )

    return app


celery_app = _build_celery_app()

# Keep `app` alias so `celery -A src.app.worker worker ...` works.
app = celery_app
