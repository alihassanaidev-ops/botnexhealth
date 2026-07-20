"""Unit tests for Plan 09 NexHealth subscription lifecycle/health service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.nexhealth_subscription_service import (
    DEFAULT_APPOINTMENT_EVENTS,
    DEFAULT_PATIENT_EVENTS,
    DEFAULT_SYNC_STATUS_EVENTS,
    DEFAULT_WEBHOOK_EVENTS,
    NexHealthSubscriptionLifecycleService,
    _resource_type_for_event,
)


def _session(result) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_ensure_location_subscription_creates_pending_row_without_callback():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session(result)
    svc = NexHealthSubscriptionLifecycleService(session)

    row, created = await svc.ensure_location_subscription(
        institution=SimpleNamespace(id="inst-1"),
        location=SimpleNamespace(
            id="loc-1",
            nexhealth_subdomain="sub",
            nexhealth_location_id="nh-loc",
        ),
    )

    assert created is True
    assert row.status == NexHealthWebhookSubscriptionStatus.PENDING.value
    assert row.event_types == DEFAULT_WEBHOOK_EVENTS
    assert DEFAULT_APPOINTMENT_EVENTS == [
        "appointment_insertion",
        "appointment_created",
        "appointment_updated",
    ]
    assert DEFAULT_PATIENT_EVENTS == ["patient_created", "patient_updated"]
    assert DEFAULT_SYNC_STATUS_EVENTS == [
        "sync_status_read_change",
        "sync_status_write_change",
    ]
    session.add.assert_called_once()


def test_resource_type_for_patient_and_appointment_events():
    assert _resource_type_for_event("appointment_created") == "Appointment"
    assert _resource_type_for_event("appointment_insertion.complete") == "Appointment"
    assert _resource_type_for_event("patient_created") == "Patient"
    assert _resource_type_for_event("patient_updated") == "Patient"
    assert _resource_type_for_event("sync_status_read_change") == "SyncStatus"
    assert _resource_type_for_event("sync_status_write_change") == "SyncStatus"


@pytest.mark.asyncio
async def test_record_event_seen_marks_active_when_provider_id_exists():
    row = SimpleNamespace(
        provider_subscription_id="provider-1",
        status=NexHealthWebhookSubscriptionStatus.PENDING.value,
        last_event_at=None,
        last_health_check_at=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    svc = NexHealthSubscriptionLifecycleService(_session(result))

    await svc.record_event_seen(institution_id="inst-1", location_id="loc-1")

    assert row.status == NexHealthWebhookSubscriptionStatus.ACTIVE.value
    assert row.last_event_at is not None
    assert row.last_health_check_at is not None


@pytest.mark.asyncio
async def test_health_check_marks_stale_active_subscription_failed():
    stale = SimpleNamespace(
        status=NexHealthWebhookSubscriptionStatus.ACTIVE.value,
        last_event_at=datetime.now(timezone.utc) - timedelta(hours=48),
        last_health_check_at=None,
        updated_at=None,
        error_metadata=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [stale]
    svc = NexHealthSubscriptionLifecycleService(_session(result))

    summary = await svc.health_check(stale_after_hours=24)

    assert stale.status == NexHealthWebhookSubscriptionStatus.FAILED.value
    assert stale.error_metadata["reason"] == "stale_webhook_events"
    assert summary.failed == 1
    assert summary.stale_marked == 1


@pytest.mark.asyncio
async def test_health_check_marks_active_subscription_failed_when_no_events_seen():
    stale = SimpleNamespace(
        status=NexHealthWebhookSubscriptionStatus.ACTIVE.value,
        last_event_at=None,
        last_health_check_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=48),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=48),
        error_metadata=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [stale]
    svc = NexHealthSubscriptionLifecycleService(_session(result))

    summary = await svc.health_check(stale_after_hours=24)

    assert stale.status == NexHealthWebhookSubscriptionStatus.FAILED.value
    assert stale.error_metadata["reason"] == "no_webhook_events_seen"
    assert summary.failed == 1
    assert summary.stale_marked == 1


@pytest.mark.asyncio
async def test_active_or_pending_targets_returns_subscription_ids():
    rows = [
        SimpleNamespace(institution_id="inst-1", id="sub-1"),
        SimpleNamespace(institution_id="inst-2", id="sub-2"),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    svc = NexHealthSubscriptionLifecycleService(_session(result))

    assert await svc.active_or_pending_targets() == [
        ("inst-1", "sub-1"),
        ("inst-2", "sub-2"),
    ]
