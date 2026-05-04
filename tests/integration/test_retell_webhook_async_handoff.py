"""Integration-style assertions on the async webhook handoff.

The unit tests in ``test_retell_webhooks_idempotency.py`` cover the
shape of the handoff (handler enqueues, helper finalizes). The tests
here pin two operational contracts that wouldn't show up in a unit
test of either piece in isolation:

  1. Handler returns fast under realistic mocking — even if the
     post-claim helper would take seconds, the handler must NOT
     await it inline. The whole point of the refactor.

  2. The Celery task wrapper actually calls the helper. A drift in
     names or shapes would silently break the production path and
     this test would catch it locally before the deploy.

Both tests run without needing a worker process: we mock the queue
side. Production semantics (``delay`` actually queues) are exercised
in the local validation harness.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.retell import webhooks


def _payload(call_id: str) -> bytes:
    return json.dumps(
        {
            "event": "call_analyzed",
            "call": {
                "call_id": call_id,
                "agent_id": "agent-async-test",
                "from_number": "+14165550000",
                "duration_ms": 60000,
                "recording_url": "https://example.test/rec.mp3",
                "transcript": "test",
                "call_analysis": {"call_summary": "[redacted]"},
                "scrubbed_call_analysis": {"call_summary": "[redacted]"},
            },
        }
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_handler_returns_under_200ms_when_post_claim_work_is_slow():
    """The handler must NOT await the post-claim helper inline.

    We mock the helper to a 2-second sleep. Under the async-handoff
    design the helper is never awaited from the request thread —
    only enqueued via ``task.delay``. So the handler's wall-clock
    time stays small (~10s of ms) regardless.

    Threshold of 200ms is generous against test-machine variance
    while still catching the regression — a synchronous-inlined
    helper would clock at ~2000ms.
    """
    call_id = f"async-test-{int(time.time() * 1000)}"

    slow_helper = AsyncMock()

    async def slow_run(_payload):
        import asyncio

        await asyncio.sleep(2.0)
        return {"status": "should_not_be_seen"}

    slow_helper.side_effect = slow_run

    delay = MagicMock()
    fake_task = MagicMock(delay=delay)

    # All three patched: claim, helper, dispatcher. The claim is mocked
    # because this test cares about the handler's flow-control timing,
    # not Postgres latency.
    with (
        patch.object(
            webhooks,
            "_begin_webhook_processing",
            new=AsyncMock(return_value=(True, "new_event")),
        ),
        patch.object(
            webhooks, "process_retell_call_analyzed_event", new=slow_helper
        ),
        patch(
            "src.app.tasks.webhooks.process_retell_call_analyzed", fake_task
        ),
    ):
        start = time.monotonic()
        result = await webhooks.handle_retell_webhook(_payload(call_id))
        elapsed_ms = (time.monotonic() - start) * 1000

    assert result["status"] == "queued", (
        f"Expected 'queued', got {result!r} — handler may have run helper inline"
    )
    delay.assert_called_once()
    slow_helper.assert_not_called(), (
        "Helper was called inline — async handoff is broken; the slow "
        "helper would block the handler"
    )
    assert elapsed_ms < 200, (
        f"Handler took {elapsed_ms:.1f}ms — expected <200ms, suggesting "
        f"some inline work crept back in"
    )


def test_celery_task_invokes_helper_with_payload():
    """The Celery task body wraps the helper in ``asyncio.run``.

    A drift in the helper's signature, return shape, or import path
    would silently break the production task body. This sync test
    invokes ``.run()`` (the task callable) directly and asserts the
    helper is called with the same payload the dispatcher sees.

    Run as a sync function (no ``@pytest.mark.asyncio``) because the
    task body uses ``asyncio.run()`` which is incompatible with an
    already-running event loop.
    """
    from src.app.tasks import webhooks as webhook_tasks

    fake_helper = AsyncMock(return_value={"status": "success", "via": "task"})

    with patch(
        "src.app.retell.webhooks.process_retell_call_analyzed_event",
        new=fake_helper,
    ):
        result = webhook_tasks.process_retell_call_analyzed.run(
            {"event": "call_analyzed", "call": {"call_id": "task-1"}}
        )

    assert result == {"status": "success", "via": "task"}
    fake_helper.assert_called_once()
    assert fake_helper.call_args.args[0]["call"]["call_id"] == "task-1"
