"""Unit tests for GoTracker webhook subscription lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.app.models.gotracker_webhook_subscription import (
    GoTrackerWebhookSubscriptionStatus,
)
from src.app.pms.gotracker.client import GoTrackerAPIError
from src.app.services.automation.gotracker_subscription_service import (
    DEFAULT_GOTRACKER_WEBHOOK_EVENTS,
    GoTrackerSubscriptionReconnectError,
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
            {"data": []},
            {"data": {"id": "sub-1", "secret": "returned-secret"}},
        ]
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        location = SimpleNamespace(
            id="loc-1",
            gotracker_webhook_secret=None,
            gotracker_webhook_secret_encrypted=None,
        )
        row, created = await svc.ensure_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=location,
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert created is True
    assert row.provider_subscription_id == "sub-1"
    assert row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    assert location.gotracker_webhook_secret == "returned-secret"
    assert client.request.await_count == 2
    client.request.assert_any_await("GET", "/api/webhooks/subscriptions")
    client.request.assert_any_await(
        "POST",
        "/api/webhooks/subscriptions",
        json={
            "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
            "event_types": ",".join(DEFAULT_GOTRACKER_WEBHOOK_EVENTS),
            "is_active": True,
        },
    )
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_location_subscription_adopts_existing_remote_subscription():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        side_effect=[
            {
                "data": [
                    {
                        "id": "14",
                        "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
                    }
                ]
            },
            {"data": {"id": "14"}},
        ]
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        row, _ = await svc.ensure_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1", gotracker_webhook_secret="secret"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert row.provider_subscription_id == "14"
    assert row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    client.request.assert_any_await(
        "PATCH",
        "/api/webhooks/subscriptions/14",
        json={
            "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
            "event_types": ",".join(DEFAULT_GOTRACKER_WEBHOOK_EVENTS),
            "is_active": True,
        },
    )


@pytest.mark.asyncio
async def test_reconnect_rotates_and_stores_secret_for_existing_subscription():
    row = SimpleNamespace(
        provider_subscription_id="14",
        callback_url=None,
        event_types=[],
        status="failed",
        error_metadata={"reason": "signature_mismatch"},
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = _session(result)
    persistence_order: list[str] = []
    session.commit.side_effect = lambda: persistence_order.append("commit")
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        return_value={"subscription_id": "14", "secret": "rotated-secret"}
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock(
        side_effect=lambda: persistence_order.append("adapter_close")
    )
    location = SimpleNamespace(id="loc-1", gotracker_webhook_secret="old-secret")

    with patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        reconnect = await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=location,
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert reconnect.action == "rotated"
    assert reconnect.subscription is row
    assert location.gotracker_webhook_secret == "rotated-secret"
    assert row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    client.request.assert_awaited_once_with(
        "POST", "/api/webhooks/subscriptions/14/rotate-secret"
    )
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    adapter.close.assert_awaited_once()
    assert persistence_order == ["commit", "adapter_close"]


@pytest.mark.asyncio
async def test_reconnect_creates_subscription_when_provider_id_is_missing():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        return_value={"data": {"subscription_id": "15", "signing_secret": "new-secret"}}
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()
    location = SimpleNamespace(id="loc-1", gotracker_webhook_secret="stale-secret")

    with patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        reconnect = await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=location,
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert reconnect.action == "created"
    assert reconnect.subscription.provider_subscription_id == "15"
    assert location.gotracker_webhook_secret == "new-secret"
    client.request.assert_awaited_once_with(
        "POST",
        "/api/webhooks/subscriptions",
        json={
            "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
            "event_types": ",".join(DEFAULT_GOTRACKER_WEBHOOK_EVENTS),
            "is_active": True,
        },
    )
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_creates_replacement_when_stored_subscription_is_gone():
    row = SimpleNamespace(
        provider_subscription_id="stale-14",
        callback_url=None,
        event_types=[],
        status="failed",
        error_metadata={"reason": "signature_mismatch"},
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        side_effect=[
            GoTrackerAPIError("Subscription not found", status_code=404),
            {"subscription_id": "new-15", "secret": "replacement-secret"},
        ]
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()
    location = SimpleNamespace(id="loc-1", gotracker_webhook_secret="old-secret")

    with patch(
        "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
        new=AsyncMock(return_value=adapter),
    ):
        reconnect = await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=location,
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    assert reconnect.action == "created"
    assert row.provider_subscription_id == "new-15"
    assert location.gotracker_webhook_secret == "replacement-secret"
    assert client.request.await_args_list == [
        call("POST", "/api/webhooks/subscriptions/stale-14/rotate-secret"),
        call(
            "POST",
            "/api/webhooks/subscriptions",
            json={
                "url": "https://api.example.com/api/v1/gotracker/webhooks/loc-1",
                "event_types": ",".join(DEFAULT_GOTRACKER_WEBHOOK_EVENTS),
                "is_active": True,
            },
        ),
    ]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_does_not_create_replacement_for_non_404_rotation_error():
    row = SimpleNamespace(
        provider_subscription_id="14",
        callback_url=None,
        event_types=[],
        status="active",
        error_metadata=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = _session(result)
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(
        side_effect=GoTrackerAPIError("Forbidden", status_code=403)
    )
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
            new=AsyncMock(return_value=adapter),
        ),
        pytest.raises(GoTrackerSubscriptionReconnectError),
    ):
        await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1", gotracker_webhook_secret="old"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    client.request.assert_awaited_once_with(
        "POST", "/api/webhooks/subscriptions/14/rotate-secret"
    )
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_rejects_rotation_without_returned_secret():
    row = SimpleNamespace(
        provider_subscription_id="14",
        callback_url=None,
        event_types=[],
        status="active",
        error_metadata=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    svc = GoTrackerSubscriptionLifecycleService(_session(result))
    client = AsyncMock()
    client.request = AsyncMock(return_value={"subscription_id": "14"})
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
            new=AsyncMock(return_value=adapter),
        ),
        pytest.raises(GoTrackerSubscriptionReconnectError),
    ):
        await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1", gotracker_webhook_secret="old"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )


@pytest.mark.asyncio
async def test_reconnect_treats_credential_commit_failure_as_hard_error():
    row = SimpleNamespace(
        provider_subscription_id="14",
        callback_url=None,
        event_types=[],
        status="active",
        error_metadata=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = _session(result)
    session.commit.side_effect = RuntimeError("database unavailable")
    svc = GoTrackerSubscriptionLifecycleService(session)
    client = AsyncMock()
    client.request = AsyncMock(return_value={"secret": "rotated-secret"})
    adapter = MagicMock()
    adapter._client = client
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.pms.gotracker.adapter.GoTrackerAdapter.create",
            new=AsyncMock(return_value=adapter),
        ),
        pytest.raises(
            GoTrackerSubscriptionReconnectError,
            match="credentials could not be saved",
        ),
    ):
        await svc.reconnect_location_subscription(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1", gotracker_webhook_secret="old"),
            callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
        )

    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    adapter.close.assert_awaited_once()


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
