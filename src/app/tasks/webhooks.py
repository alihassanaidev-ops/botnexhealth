"""Celery tasks that process webhook payloads asynchronously.

The corresponding HTTP handlers (``src/app/retell/webhooks.py``,
``src/app/api/routes/twilio_webhooks.py``) verify the vendor
signature, claim the idempotency row, enqueue a task here, and return
200 in <50ms. The actual side-effects — DB writes, downstream
notifications, audit rows — happen on a Celery worker so a slow
DB query never wedges the API request queue.

Routing: every task in this module lives on the ``webhooks`` queue
(see ``src/app/worker.py``). A backlog of call-analyzed events
therefore competes only with itself; the notifications/SMS queues
keep their own worker capacity.

Retries: ``autoretry_for=(Exception,)`` + exponential backoff. After
``max_retries`` Celery drops the task; by then the
:func:`src.app.retell.webhooks.process_retell_call_analyzed_event`
helper has already marked the idempotency row FAILED and captured a
``dead_letter_events`` row, which is the operator signal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="webhooks.process_retell_call_analyzed",
    queue="webhooks",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,  # cap individual back-offs at 10 minutes
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def process_retell_call_analyzed(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Process a Retell ``call_analyzed`` payload off the request thread.

    The webhook handler has already validated the signature, parsed
    the JSON, and committed the PROCESSING idempotency row. We pick
    up exactly where it left off — agent resolution, ``PostCallService``
    writes, downstream enqueues, audit row, idempotency finalize.

    Returns the helper's result dict (visible in worker logs); on any
    exception the helper's own try/except marks the idempotency row
    FAILED + writes a FAILURE audit + DLQs, then re-raises. Celery
    catches the re-raised exception and triggers ``autoretry_for``.
    """
    # Lazy DB init — Celery worker processes don't run the FastAPI
    # startup hook, so the global engine + session_factory are unset
    # on a fresh worker. Other tasks (notifications, sms, recordings)
    # use the same gate; without it the first task on each new worker
    # process raises ``Database not initialized``.
    from src.app.config import settings
    from src.app.database import init_database, is_database_initialized

    if not is_database_initialized():
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is required to process Retell webhooks"
            )
        init_database(settings.database_url)

    # Lazy import keeps worker startup minimal and avoids importing
    # FastAPI at task-load time.
    from src.app.retell.webhooks import process_retell_call_analyzed_event

    return asyncio.run(process_retell_call_analyzed_event(payload))


@celery_app.task(
    bind=True,
    name="webhooks.process_retell_call_ended",
    queue="webhooks",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def process_retell_call_ended(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Process a Retell ``call_ended`` payload off the request thread.

    Sends the patient the appointment-confirmation SMS (Approach B: our own
    template populated from the authoritative PMS booking) as soon as the call
    ends, rather than waiting for the delayed ``call_analyzed`` analysis. Only
    fires when an appointment was actually booked during the call.
    """
    from src.app.config import settings
    from src.app.database import init_database, is_database_initialized

    if not is_database_initialized():
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is required to process Retell webhooks"
            )
        init_database(settings.database_url)

    from src.app.retell.webhooks import process_retell_call_ended_event

    return asyncio.run(process_retell_call_ended_event(payload))
