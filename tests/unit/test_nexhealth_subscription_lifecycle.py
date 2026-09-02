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
    nexhealth_live_callback_url,
    _resource_type_for_event,
)


def _session(result) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _compiled_sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _assert_nexhealth_pms_filter(stmt) -> None:
    sql = _compiled_sql(stmt)
    assert "institutions.pms_type = 'nexhealth'" in sql


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
    session = _session(result)
    svc = NexHealthSubscriptionLifecycleService(session)

    assert await svc.active_or_pending_targets() == [
        ("inst-1", "sub-1"),
        ("inst-2", "sub-2"),
    ]
    _assert_nexhealth_pms_filter(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_ensure_for_configured_locations_only_targets_nexhealth_institutions():
    result = MagicMock()
    result.all.return_value = []
    session = _session(result)

    await NexHealthSubscriptionLifecycleService(
        session
    ).ensure_for_configured_locations()

    _assert_nexhealth_pms_filter(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_scheduled_ensure_does_not_create_unapproved_remote_connection():
    location = SimpleNamespace(
        id="loc-1", nexhealth_subdomain="practice", nexhealth_location_id="11"
    )
    institution = SimpleNamespace(id="inst-1")
    result = MagicMock()
    result.all.return_value = [(location, institution)]
    svc = NexHealthSubscriptionLifecycleService(_session(result))
    row = SimpleNamespace(
        provider_subscription_id=None,
        api_key_hash="hash-1",
        credential_mode="platform",
        status=NexHealthWebhookSubscriptionStatus.PENDING.value,
    )
    svc.ensure_location_subscription = AsyncMock(return_value=(row, True))
    svc._ensure_remote_group = AsyncMock()

    await svc.ensure_for_configured_locations(
        callback_url="https://api.example.test/api/v1/nexhealth/webhooks/appointments",
        create_missing_remote=False,
    )

    svc._ensure_remote_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_subscription_targets_only_returns_nexhealth_institutions():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = _session(result)

    await NexHealthSubscriptionLifecycleService(
        session
    ).configured_subscription_targets()

    _assert_nexhealth_pms_filter(session.execute.await_args.args[0])


def test_live_callback_uses_public_api_url_when_no_override():
    assert (
        nexhealth_live_callback_url(
            public_api_url="https://api.staging.scalenexus.ai/",
            explicit_callback_url=None,
        )
        == "https://api.staging.scalenexus.ai/api/v1/nexhealth/webhooks/appointments"
    )


@pytest.mark.asyncio
async def test_institution_setup_groups_sibling_locations_by_subdomain():
    locations = [
        SimpleNamespace(
            id="loc-1", nexhealth_subdomain="practice", nexhealth_location_id="11"
        ),
        SimpleNamespace(
            id="loc-2", nexhealth_subdomain="practice", nexhealth_location_id="12"
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = locations
    svc = NexHealthSubscriptionLifecycleService(_session(result))
    rows = [SimpleNamespace(), SimpleNamespace()]
    svc.ensure_location_subscription = AsyncMock(
        side_effect=[(rows[0], True), (rows[1], True)]
    )
    svc._ensure_remote_group = AsyncMock()
    institution = SimpleNamespace(id="inst-1")

    returned = await svc.ensure_for_institution(
        institution=institution,
        callback_url="https://api.example.test/api/v1/nexhealth/webhooks/appointments",
    )

    assert returned == rows
    svc._ensure_remote_group.assert_awaited_once()
    assert svc._ensure_remote_group.await_args.kwargs["rows"] == rows


@pytest.mark.asyncio
async def test_remote_group_creates_one_endpoint_and_all_required_subscriptions():
    rows = [
        SimpleNamespace(
            provider_subscription_id=None,
            provider_subscription_ids=[],
            callback_url=None,
            credential_mode=None,
            api_key_hash=None,
            status="pending",
            error_metadata=None,
            last_health_check_at=None,
            updated_at=None,
            secret_key=None,
        )
        for _ in range(2)
    ]
    adapter = SimpleNamespace(
        _client=object(),
        credential_mode="platform",
        api_key_hash="hash-1",
        _default_params=lambda: {"subdomain": "practice"},
        close=AsyncMock(),
    )
    next_subscription_id = 100

    async def provider_call(_client, method, path, **kwargs):
        nonlocal next_subscription_id
        if method == "POST" and path == "/webhook_endpoints":
            return {"data": {"id": 77, "secret_key": "signing-secret"}}
        if method == "GET" and path.endswith("/webhook_subscriptions"):
            return {"data": []}
        if method == "POST" and path.endswith("/webhook_subscriptions"):
            next_subscription_id += 1
            return {"data": {"id": next_subscription_id, **kwargs["json"]}}
        raise AssertionError((method, path, kwargs))

    session = AsyncMock()
    managed_result = MagicMock()
    managed_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=managed_result)
    svc = NexHealthSubscriptionLifecycleService(session)
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        monkeypatch.setattr(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        )
        monkeypatch.setattr(
            "src.app.api.helpers.handle_nexhealth_request", provider_call
        )
        await svc._ensure_remote_group(
            rows=rows,
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(nexhealth_subdomain="practice"),
            callback_url="https://api.example.test/api/v1/nexhealth/webhooks/appointments",
            event_types=DEFAULT_WEBHOOK_EVENTS,
        )

    assert {row.provider_subscription_id for row in rows} == {"77"}
    assert all(row.secret_key == "signing-secret" for row in rows)
    assert all(row.status == "active" for row in rows)
    assert all(
        len(row.provider_subscription_ids) == len(DEFAULT_WEBHOOK_EVENTS)
        for row in rows
    )
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_group_reuses_account_endpoint_for_another_subdomain():
    row = SimpleNamespace(
        provider_subscription_id=None,
        provider_subscription_ids=[],
        callback_url=None,
        credential_mode="platform",
        api_key_hash="hash-1",
        status="pending",
        error_metadata=None,
        last_health_check_at=None,
        updated_at=None,
        secret_key=None,
    )
    managed = SimpleNamespace(
        provider_subscription_id="77",
        secret_key="shared-signing-secret",
    )
    managed_result = MagicMock()
    managed_result.scalar_one_or_none.return_value = managed
    session = _session(managed_result)
    adapter = SimpleNamespace(
        _client=object(),
        credential_mode="platform",
        api_key_hash="hash-1",
        _default_params=lambda: {"subdomain": "second-practice"},
        close=AsyncMock(),
    )
    calls: list[tuple[str, str]] = []
    next_subscription_id = 200

    async def provider_call(_client, method, path, **kwargs):
        nonlocal next_subscription_id
        calls.append((method, path))
        if method == "PATCH" and path == "/webhook_endpoints/77":
            return {"data": {"id": 77}}
        if method == "GET" and path.endswith("/webhook_subscriptions"):
            assert kwargs["params"]["subdomain"] == "second-practice"
            return {"data": []}
        if method == "POST" and path.endswith("/webhook_subscriptions"):
            next_subscription_id += 1
            return {"data": {"id": next_subscription_id, **kwargs["json"]}}
        raise AssertionError((method, path, kwargs))

    svc = NexHealthSubscriptionLifecycleService(session)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        )
        monkeypatch.setattr(
            "src.app.api.helpers.handle_nexhealth_request", provider_call
        )
        await svc._ensure_remote_group(
            rows=[row],
            institution=SimpleNamespace(id="inst-2"),
            location=SimpleNamespace(nexhealth_subdomain="second-practice"),
            callback_url="https://api.example.test/api/v1/nexhealth/webhooks/appointments",
            event_types=DEFAULT_WEBHOOK_EVENTS,
        )

    assert ("POST", "/webhook_endpoints") not in calls
    assert row.provider_subscription_id == "77"
    assert row.secret_key == "shared-signing-secret"
    assert len(row.provider_subscription_ids) == len(DEFAULT_WEBHOOK_EVENTS)
