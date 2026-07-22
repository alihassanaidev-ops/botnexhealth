"""Unit tests for GoTracker webhook subscription lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.gotracker_webhook_subscription import (
    GoTrackerWebhookSubscriptionStatus,
)
from src.app.services.automation.gotracker_subscription_service import (
    DEFAULT_GOTRACKER_WEBHOOK_EVENTS,
    GoTrackerSubscriptionLifecycleService,
    _extract_provider_subscription_id,
    _location_callback_url,
)


def _session(result) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_ensure_location_subscription_creates_pending_without_callback():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)

    row, created = await svc.ensure_location_subscription(
        institution=SimpleNamespace(id="inst-1"),
        location=SimpleNamespace(id="loc-1"),
    )

    assert created is True
    assert row.status == GoTrackerWebhookSubscriptionStatus.PENDING.value
    assert row.event_types == DEFAULT_GOTRACKER_WEBHOOK_EVENTS
    assert row.callback_url is None
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_location_subscription_posts_to_synchronizer():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        side_effect=[
            {"data": {"id": f"sub-{index}"}}
            for index, _ in enumerate(DEFAULT_GOTRACKER_WEBHOOK_EVENTS, start=1)
        ]
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with patch(
        "src.app.services.automation.gotracker_subscription_service.settings"
    ) as mock_settings, patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        mock_settings.gotracker_webhook_secret = "shared-secret"
        row, created = await svc.ensure_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert created is True
    assert row.provider_subscription_id == "sub-1,sub-2,sub-3,sub-4,sub-5"
    assert row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    assert client.request.await_count == len(DEFAULT_GOTRACKER_WEBHOOK_EVENTS)
    client.request.assert_any_await(
        "POST",
        "/api/webhooks/subscriptions",
        json={
            "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
            "event_types": "appointment.created",
            "secret": "shared-secret",
        },
    )
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_create_failure_marks_failed():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)

    with patch(
        "src.app.services.automation.gotracker_subscription_service.settings"
    ) as mock_settings:
        mock_settings.gotracker_webhook_secret = ""
        row, _ = await svc.ensure_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert row.status == GoTrackerWebhookSubscriptionStatus.FAILED.value
    assert row.error_metadata == {"reason": "missing_gotracker_webhook_secret"}


@pytest.mark.asyncio
async def test_record_event_seen_marks_active_when_provider_id_exists():
    row = SimpleNamespace(
        provider_subscription_id="sub-1",
        status=GoTrackerWebhookSubscriptionStatus.PENDING.value,
        last_event_at=None,
        last_health_check_at=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    svc = GoTrackerSubscriptionLifecycleService(_session(result))

    await svc.record_event_seen(institution_id="inst-1", location_id="loc-1")

    assert row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    assert row.last_event_at is not None
    assert row.last_health_check_at is not None


@pytest.mark.asyncio
async def test_health_check_marks_stale_active_subscription_failed():
    stale = SimpleNamespace(
        status=GoTrackerWebhookSubscriptionStatus.ACTIVE.value,
        last_event_at=datetime.now(timezone.utc) - timedelta(hours=48),
        last_health_check_at=None,
        updated_at=None,
        error_metadata=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [stale]
    svc = GoTrackerSubscriptionLifecycleService(_session(result))

    summary = await svc.health_check(stale_after_hours=24)

    assert stale.status == GoTrackerWebhookSubscriptionStatus.FAILED.value
    assert stale.error_metadata["reason"] == "stale_webhook_events"
    assert summary.failed == 1
    assert summary.stale_marked == 1


def test_location_callback_url_is_location_scoped():
    assert (
        _location_callback_url("https://api.example.com/", "loc-1")
        == "https://api.example.com/api/v1/gotracker/webhooks/loc-1"
    )


def test_extract_provider_subscription_id_accepts_nested_shapes():
    assert _extract_provider_subscription_id({"id": "sub-1"}) == "sub-1"
    assert _extract_provider_subscription_id({"data": {"subscription_id": 2}}) == "2"
    assert (
        _extract_provider_subscription_id({"data": {"subscription": {"webhook_id": 3}}})
        == "3"
    )
