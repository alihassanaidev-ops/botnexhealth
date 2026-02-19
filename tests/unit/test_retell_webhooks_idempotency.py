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
                "call_analysis": {"call_summary": "test summary"},
            },
        }
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_webhook_duplicate_skips_side_effects():
    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(False, "already_completed"))), \
         patch.object(webhooks, "get_tenant_ghl_client", new=AsyncMock()) as get_client:
        result = await webhooks.handle_retell_webhook(_payload())
        assert result["status"] == "duplicate"
        get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_success_marks_completed_and_publishes_event():
    mock_ghl = MagicMock()
    mock_ghl.upsert_contact_from_retell = AsyncMock(
        return_value={"contact": {"id": "ghl-123"}, "new": True}
    )
    tenant = MagicMock()
    tenant.id = "tenant-1"
    tenant.slug = "acme-dental"

    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(True, "new_event"))), \
         patch.object(webhooks, "_finish_webhook_processing", new=AsyncMock()) as finish, \
         patch.object(webhooks, "_publish_call_data_event", new=AsyncMock()) as publish, \
         patch.object(webhooks, "get_tenant_ghl_client", new=AsyncMock(return_value=(mock_ghl, tenant))):
        result = await webhooks.handle_retell_webhook(_payload())

    assert result["status"] == "success"
    assert result["ghl_contact_id"] == "ghl-123"
    finish.assert_awaited()
    publish.assert_awaited_once()
    event_payload = publish.await_args.kwargs
    assert event_payload["tenant_id"] == "tenant-1"
    assert event_payload["event_type"] == "data_changed"


@pytest.mark.asyncio
async def test_webhook_ghl_error_marks_failed_and_emits_sync_error():
    mock_ghl = MagicMock()
    mock_ghl.upsert_contact_from_retell = AsyncMock(side_effect=Exception("GHL down"))
    tenant = MagicMock()
    tenant.id = "tenant-1"
    tenant.slug = "acme-dental"

    with patch.object(webhooks, "_begin_webhook_processing", new=AsyncMock(return_value=(True, "new_event"))), \
         patch.object(webhooks, "_finish_webhook_processing", new=AsyncMock()) as finish, \
         patch.object(webhooks, "_publish_call_data_event", new=AsyncMock()) as publish, \
         patch.object(webhooks, "get_tenant_ghl_client", new=AsyncMock(return_value=(mock_ghl, tenant))):
        result = await webhooks.handle_retell_webhook(_payload())

    assert result["status"] == "error"
    finish.assert_awaited()
    kwargs = finish.await_args.kwargs
    assert kwargs["status"] == "FAILED"
    publish.assert_awaited_once()
    publish_kwargs = publish.await_args.kwargs
    assert publish_kwargs["event_type"] == "data_sync_error"

