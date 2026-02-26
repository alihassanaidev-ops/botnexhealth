"""Unit tests for Retell webhook idempotency and event signaling."""

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


@pytest.mark.asyncio
async def test_webhook_duplicate_skips_side_effects():
    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(False, "already_completed"))):
        result = await webhooks.handle_retell_webhook(_payload())
        assert result["status"] == "duplicate"


@pytest.mark.asyncio
async def test_webhook_success_marks_completed():
    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(True, "new_event"))), \
         patch.object(webhooks, "_finish_webhook_processing", new=AsyncMock()) as finish:
        result = await webhooks.handle_retell_webhook(_payload())

    assert result["status"] == "success"
    finish.assert_awaited()
