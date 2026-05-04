"""Unit tests for the Retell webhook handler + post-claim helper.

After the async-handoff refactor (item 2 of the scale-readiness work),
the request handler does only signature verify + parse + idempotency
claim + Celery enqueue, and the actual call-analyzed processing lives
in :func:`src.app.retell.webhooks.process_retell_call_analyzed_event`
(driven from a Celery task in production, callable directly here).

These tests cover both halves of the contract:

  - Handler: returns the right status string, calls task.delay exactly
    once on success, refuses to enqueue on duplicates, returns fast.
  - Helper: marks COMPLETED on success, marks FAILED + DLQs +
    re-raises on lookup error (so Celery's autoretry kicks in).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.retell import webhooks


def _payload(call_id: str = "call-1") -> bytes:
    return json.dumps(
        {
            "event": "call_analyzed",
            "call": {
                "call_id": call_id,
                "agent_id": "agent-1",
                "from_number": "+14165551234",
                "duration_ms": 120000,
                "recording_url": "https://example.com/rec.mp3",
                "transcript": "test transcript",
                "call_analysis": {"call_summary": "Patient Name: John Doe scheduled an appointment"},
                "scrubbed_call_analysis": {"call_summary": "[Patient Name] scheduled an appointment"},
            },
        }
    ).encode("utf-8")


# ── Handler ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_returns_duplicate_without_enqueueing():
    """A duplicate webhook (already-COMPLETED idempotency row) must NOT
    enqueue a task — that would be a wasted worker invocation."""
    delay = MagicMock()
    fake_task = MagicMock(delay=delay)
    with (
        patch.object(
            webhooks,
            "_begin_webhook_processing",
            new=AsyncMock(return_value=(False, "already_completed")),
        ),
        patch(
            "src.app.tasks.webhooks.process_retell_call_analyzed",
            fake_task,
        ),
    ):
        result = await webhooks.handle_retell_webhook(_payload())

    assert result["status"] == "duplicate"
    assert result["reason"] == "already_completed"
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_handler_returns_ignored_for_non_call_analyzed_events():
    """Other event types (call_started, etc.) take a queue slot only if
    they're actually meaningful. Filter them out before claim/enqueue."""
    body = json.dumps(
        {"event": "call_started", "call": {"call_id": "x", "agent_id": "a"}}
    ).encode("utf-8")
    delay = MagicMock()
    fake_task = MagicMock(delay=delay)
    with patch(
        "src.app.tasks.webhooks.process_retell_call_analyzed", fake_task
    ):
        result = await webhooks.handle_retell_webhook(body)

    assert result["status"] == "ignored"
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_handler_enqueues_task_and_returns_queued_on_first_event():
    """The hot-path success case: claim + enqueue + return ``queued``.

    The handler must NOT do any of the post-claim processing inline —
    that's the whole point of the async refactor. We assert that the
    Celery task was delayed exactly once with the parsed payload.
    """
    delay = MagicMock()
    fake_task = MagicMock(delay=delay)
    with (
        patch.object(
            webhooks,
            "_begin_webhook_processing",
            new=AsyncMock(return_value=(True, "new_event")),
        ),
        patch(
            "src.app.tasks.webhooks.process_retell_call_analyzed",
            fake_task,
        ),
    ):
        result = await webhooks.handle_retell_webhook(_payload("call-7"))

    assert result["status"] == "queued"
    assert result["call_id"] == webhooks.hash_for_logging("call-7")
    delay.assert_called_once()
    queued_payload = delay.call_args.args[0]
    assert queued_payload["call"]["call_id"] == "call-7"


@pytest.mark.asyncio
async def test_handler_returns_400_on_unparseable_body():
    """Bad JSON must NOT 500. Vendor signature was already verified;
    the wrong payload at this point is a bug on the vendor side that
    we surface as a 400 so they fix it."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await webhooks.handle_retell_webhook(b"this is not json")

    assert exc.value.status_code == 400


# ── process_retell_call_analyzed_event helper ───────────────────────


@pytest.mark.asyncio
async def test_helper_returns_success_and_finalizes_completed_when_no_institution():
    """If the agent_id doesn't resolve to an institution, that's a
    configuration no-op (not an error) — the helper still marks the
    idempotency row COMPLETED, audits SUCCESS, and returns success."""
    finish = AsyncMock()
    with (
        patch.object(
            webhooks,
            "_resolve_institution_location_from_agent",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch.object(webhooks, "_finish_webhook_processing", new=finish),
        patch("src.app.services.audit.log_audit_background"),
    ):
        result = await webhooks.process_retell_call_analyzed_event(
            json.loads(_payload())
        )

    assert result["status"] == "success"
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_helper_marks_failed_and_reraises_on_lookup_error():
    """A retryable lookup error (e.g., transient NexHealth outage) must:
      1. Mark the idempotency row FAILED so a manual replay can pick it up.
      2. Capture a dead_letter_events row.
      3. RE-RAISE the original exception — Celery's autoretry depends on
         seeing the exception bubble out of the task body.

    The previous synchronous design raised an HTTPException(503). That
    return path doesn't exist anymore: the handler already returned
    200 to Retell long before this helper ran."""
    finish = AsyncMock()
    capture_dead_letter = AsyncMock()
    lookup_err = webhooks.RetellAgentLookupError(
        "Retell agent lookup failed; retry webhook"
    )

    with (
        patch.object(
            webhooks,
            "_resolve_institution_location_from_agent",
            new=AsyncMock(side_effect=lookup_err),
        ),
        patch.object(webhooks, "_finish_webhook_processing", new=finish),
        patch("src.app.services.audit.log_audit_background"),
        patch(
            "src.app.services.dead_letter.capture_dead_letter",
            new=capture_dead_letter,
        ),
    ):
        with pytest.raises(webhooks.RetellAgentLookupError):
            await webhooks.process_retell_call_analyzed_event(
                json.loads(_payload())
            )

    finish.assert_awaited_once()
    assert finish.await_args.args[:2] == ("call-1", "call_analyzed")
    assert finish.await_args.kwargs["status"] == "FAILED"
    assert "Retell agent lookup failed" in finish.await_args.kwargs["error"]
    capture_dead_letter.assert_awaited_once()
