"""Unit tests for Retell webhook idempotency and event signaling."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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
         patch.object(webhooks, "_resolve_institution_location_from_agent", new=AsyncMock(return_value=(None, None))), \
         patch.object(webhooks, "_finish_webhook_processing", new=AsyncMock()) as finish, \
         patch("src.app.services.audit.log_audit_background"):
        result = await webhooks.handle_retell_webhook(_payload())

    assert result["status"] == "success"
    finish.assert_awaited()


@pytest.mark.asyncio
async def test_webhook_lookup_error_marks_failed_and_returns_retryable():
    finish = AsyncMock()
    capture_dead_letter = AsyncMock()
    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(True, "new_event"))), \
         patch.object(
             webhooks,
             "_resolve_institution_location_from_agent",
             new=AsyncMock(side_effect=webhooks.RetellAgentLookupError("Retell agent lookup failed; retry webhook")),
         ), \
         patch.object(webhooks, "_finish_webhook_processing", new=finish), \
         patch("src.app.services.audit.log_audit_background"), \
         patch("src.app.services.dead_letter.capture_dead_letter", new=capture_dead_letter):
        with pytest.raises(HTTPException) as exc_info:
            await webhooks.handle_retell_webhook(_payload())

    assert exc_info.value.status_code == 503
    finish.assert_awaited_once()
    assert finish.await_args.args[:2] == ("call-1", "call_analyzed")
    assert finish.await_args.kwargs["status"] == "FAILED"
    assert "Retell agent lookup failed" in finish.await_args.kwargs["error"]
    capture_dead_letter.assert_awaited_once()
