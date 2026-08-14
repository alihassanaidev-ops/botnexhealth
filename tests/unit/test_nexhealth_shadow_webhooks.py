"""Unit tests for NexHealth v3 shadow webhook validation capture."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.nexhealth_webhooks import (
    nexhealth_shadow_appointment_webhook,
)
from src.app.config import settings
from src.app.models.nexhealth_webhook_shadow import (
    NexHealthWebhookShadowEvent,
    NexHealthWebhookShadowSubscription,
    NexHealthWebhookShadowSubscriptionStatus,
)
from src.app.services.automation.nexhealth_shadow_webhook_service import (
    NexHealthWebhookShadowCaptureService,
    NexHealthWebhookShadowSubscriptionService,
    SHADOW_ROUTE_APPOINTMENTS,
    SHADOW_ROUTE_PATIENTS,
    SHADOW_ROUTE_SYNC_STATUS,
    parse_shadow_payload,
    shadow_callback_url,
    shadow_signature_secrets,
)


def _sign(body: bytes, timestamp: str, secret: str = "testsecret") -> str:
    signed = f"{timestamp}.{base64.b64encode(body).decode('ascii')}"
    return hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()


def _make_request(
    raw_body: bytes, *, signature: str | None = None, timestamp: str | None = None
):
    request = MagicMock()
    request.body = AsyncMock(return_value=raw_body)
    headers: dict[str, str] = {}
    if signature:
        headers["signature"] = signature
    if timestamp:
        headers["timestamp"] = timestamp
    request.headers = headers
    return request


def _session_with_execute_results(*results):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(results))
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _scalar_locations_result(locations):
    result = MagicMock()
    result.scalars.return_value.all.return_value = locations
    return result


def _scalar_rows_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _location(
    *, institution_id="11111111-1111-1111-1111-111111111111", location_id="loc-1"
):
    return SimpleNamespace(
        id=location_id,
        institution_id=institution_id,
        nexhealth_subdomain="demo-subdomain",
        nexhealth_location_id="nexloc-1",
    )


@pytest.mark.asyncio
async def test_shadow_route_captures_parse_failure_with_200():
    raw_body = b'{"event_name":'
    request = _make_request(raw_body)
    session = _session_with_execute_results(
        _scalar_rows_result([]),
        _scalar_locations_result([]),
    )

    with (
        patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings,
        patch(
            "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
            return_value=session,
        ),
    ):
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_shadow_appointment_webhook(request)

    assert result["status"] == "captured"
    assert result["parse_status"] == "failed"
    assert result["event"] is None
    added = session.add.call_args.args[0]
    assert isinstance(added, NexHealthWebhookShadowEvent)
    assert added.raw_payload is None
    assert added.raw_payload_retain_until is None
    assert added.redacted_payload == {
        "payload": "[not_stored_unresolved_shadow_delivery]"
    }
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_shadow_route_rejects_bad_signature_before_capture():
    raw_body = b'{"event_name":"appointment_created"}'
    request = _make_request(raw_body, signature="bad", timestamp="1700000000")
    session = _session_with_execute_results(_scalar_rows_result([]))

    from fastapi import HTTPException

    with (
        patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings,
        patch(
            "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
            return_value=session,
        ),
    ):
        mock_settings.nexhealth_webhook_secret = "testsecret"
        mock_settings.is_production = False
        with pytest.raises(HTTPException) as exc:
            await nexhealth_shadow_appointment_webhook(request)

    assert exc.value.status_code == 403
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_route_accepts_stored_shadow_endpoint_secret(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    payload = {
        "event_name": "appointment_updated.complete",
        "subdomain": "demo-subdomain",
        "subscription_id": "sub-1",
        "data": {
            "appointment": {
                "id": "appt-1",
                "location_id": "nexloc-1",
                "updated_at": "2026-08-14T09:00:00Z",
            }
        },
    }
    raw_body = json.dumps(payload).encode()
    request = _make_request(
        raw_body,
        signature=_sign(raw_body, "1700000000", "shadow-secret"),
        timestamp="1700000000",
    )
    subscription_secret = SimpleNamespace(secret_key="shadow-secret")
    capture_subscription = SimpleNamespace(
        provider_endpoint_id="endpoint-1",
        provider_subscription_ids=["sub-1"],
        status=NexHealthWebhookShadowSubscriptionStatus.PENDING.value,
        last_event_at=None,
        last_health_check_at=None,
        last_shadow_capture_id=None,
        last_parse_success_at=None,
        last_parse_failure_at=None,
        parse_success_count=0,
        parse_failure_count=0,
        updated_at=None,
    )
    capture_subscription_result = MagicMock()
    capture_subscription_result.scalars.return_value.all.return_value = [
        capture_subscription
    ]
    session = _session_with_execute_results(
        _scalar_rows_result([subscription_secret]),
        _scalar_locations_result([_location()]),
        capture_subscription_result,
    )

    with (
        patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings,
        patch(
            "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
            return_value=session,
        ),
    ):
        mock_settings.nexhealth_webhook_secret = "global-secret"
        mock_settings.is_production = True
        result = await nexhealth_shadow_appointment_webhook(request)

    assert result["status"] == "captured"
    assert result["parse_status"] == "parsed"
    assert session.add.call_args.args[0].provider_subscription_id == "sub-1"


@pytest.mark.asyncio
async def test_shadow_service_stores_encrypted_raw_and_redacted_payload(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    payload = {
        "event_name": "appointment_updated.complete",
        "subdomain": "demo-subdomain",
        "subscription_id": "sub-1",
        "data": {
            "appointment": {
                "id": "appt-1",
                "location_id": "nexloc-1",
                "patient_id": "pat-1",
                "start_time": "2026-08-14T10:00:00Z",
                "updated_at": "2026-08-14T09:00:00Z",
            }
        },
    }
    raw_payload = json.dumps(payload)
    location = _location()
    subscription = SimpleNamespace(
        provider_endpoint_id="endpoint-1",
        provider_subscription_ids=["sub-1"],
        status=NexHealthWebhookShadowSubscriptionStatus.PENDING.value,
        last_event_at=None,
        last_health_check_at=None,
        last_shadow_capture_id=None,
        last_parse_success_at=None,
        last_parse_failure_at=None,
        parse_success_count=0,
        parse_failure_count=0,
        updated_at=None,
    )
    subscription_result = MagicMock()
    subscription_result.scalars.return_value.all.return_value = [subscription]
    session = _session_with_execute_results(
        _scalar_locations_result([location]),
        subscription_result,
    )

    result = await NexHealthWebhookShadowCaptureService(session).capture(
        route_family=SHADOW_ROUTE_APPOINTMENTS,
        raw_payload=raw_payload,
        parsed=parse_shadow_payload(raw_payload.encode()),
        headers={"x-nexhealth-delivery-id": "delivery-1"},
    )

    row = result.row
    assert row.parse_status == "parsed"
    assert row.institution_id == "11111111-1111-1111-1111-111111111111"
    assert row.location_id == "loc-1"
    assert row.provider_delivery_id == "delivery-1"
    assert row.provider_subscription_id == "sub-1"
    assert row.business_event_key == (
        "Appointment:appt-1:appointment_updated:updated_at:2026-08-14T09:00:00Z"
    )
    assert row.raw_payload == raw_payload
    assert row.raw_payload_encrypted is not None
    assert "appt-1" not in row.raw_payload_encrypted
    assert row.redacted_payload is not None
    assert row.redacted_payload["event_name"] == "[redacted]"
    assert subscription.status == NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value
    assert subscription.parse_success_count == 1


@pytest.mark.asyncio
async def test_shadow_service_does_not_store_raw_or_identity_when_unresolved(
    monkeypatch,
):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    payload = {
        "event_name": "appointment_updated.complete",
        "subdomain": "unknown-subdomain",
        "subscription_id": "sub-1",
        "data": {
            "appointment": {
                "id": "appt-1",
                "patient_id": "pat-1",
                "start_time": "2026-08-14T10:00:00Z",
                "updated_at": "2026-08-14T09:00:00Z",
            }
        },
    }
    raw_payload = json.dumps(payload)
    session = _session_with_execute_results(_scalar_locations_result([]))

    result = await NexHealthWebhookShadowCaptureService(session).capture(
        route_family=SHADOW_ROUTE_APPOINTMENTS,
        raw_payload=raw_payload,
        parsed=parse_shadow_payload(raw_payload.encode()),
        headers={},
    )

    row = result.row
    assert row.institution_id is None
    assert row.resolution_status == "unresolved"
    assert row.raw_payload is None
    assert row.raw_payload_retain_until is None
    assert row.redacted_payload == {
        "payload": "[not_stored_unresolved_shadow_delivery]"
    }
    assert row.pms_resource_id is None
    assert row.business_event_key is None
    assert row.extracted_identity is None


@pytest.mark.asyncio
async def test_shadow_service_extracts_syncstatus_business_identity(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    payload = {
        "event_name": "sync_status_read_change.green",
        "subdomain": "demo-subdomain",
        "subscription_id": "sub-1",
        "data": {
            "syncstatus": {
                "read_status": "green",
                "read_status_at": "2026-08-14T10:00:00Z",
                "locations": [{"id": "nexloc-1"}],
            }
        },
    }
    raw_payload = json.dumps(payload)
    location = _location()
    subscription = SimpleNamespace(
        provider_endpoint_id="endpoint-1",
        provider_subscription_ids=["sub-1"],
        status=NexHealthWebhookShadowSubscriptionStatus.PENDING.value,
        last_event_at=None,
        last_health_check_at=None,
        last_shadow_capture_id=None,
        last_parse_success_at=None,
        last_parse_failure_at=None,
        parse_success_count=0,
        parse_failure_count=0,
        updated_at=None,
    )
    subscription_result = MagicMock()
    subscription_result.scalars.return_value.all.return_value = [subscription]
    session = _session_with_execute_results(
        _scalar_locations_result([location]),
        subscription_result,
    )

    result = await NexHealthWebhookShadowCaptureService(session).capture(
        route_family=SHADOW_ROUTE_SYNC_STATUS,
        raw_payload=raw_payload,
        parsed=parse_shadow_payload(raw_payload.encode()),
        headers={},
    )

    assert result.row.resource_type == "SyncStatus"
    assert result.row.pms_resource_id == "demo-subdomain:nexloc-1"
    assert result.row.business_event_key == (
        "SyncStatus:demo-subdomain:nexloc-1:sync_status_read_change:read_status_at:2026-08-14T10:00:00Z"
    )


@pytest.mark.asyncio
async def test_shadow_subscription_service_creates_distinct_pending_route_rows():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = _session_with_execute_results(result, result, result)
    service = NexHealthWebhookShadowSubscriptionService(session)
    institution = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
    location = _location()

    rows = []
    with patch.object(service, "_try_remote_create", new=AsyncMock()):
        for route_family in (
            SHADOW_ROUTE_APPOINTMENTS,
            SHADOW_ROUTE_PATIENTS,
            SHADOW_ROUTE_SYNC_STATUS,
        ):
            row, created = await service.ensure_location_subscription(
                institution=institution,
                location=location,
                route_family=route_family,
                callback_base_url="https://api.example.com",
            )
            assert created is True
            rows.append(row)

    assert {row.route_family for row in rows} == {
        "appointments",
        "patients",
        "sync_status",
    }
    assert [row.callback_url for row in rows] == [
        "https://api.example.com/api/v1/nexhealth/webhooks/shadow/appointments",
        "https://api.example.com/api/v1/nexhealth/webhooks/shadow/patients",
        "https://api.example.com/api/v1/nexhealth/webhooks/shadow/sync-status",
    ]
    assert all(
        row.status == NexHealthWebhookShadowSubscriptionStatus.PENDING.value
        for row in rows
    )
    assert session.add.call_count == 3


@pytest.mark.asyncio
async def test_shadow_signature_secrets_decrypts_active_route_rows(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    row = SimpleNamespace(secret_key="shadow-secret")
    session = _session_with_execute_results(_scalar_rows_result([row]))

    assert await shadow_signature_secrets(
        session, route_family=SHADOW_ROUTE_APPOINTMENTS
    ) == ["shadow-secret"]


@pytest.mark.asyncio
async def test_shadow_remote_create_persists_endpoint_secret(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "shadow-test-encryption-secret")
    monkeypatch.setattr(settings, "nexhealth_api_key", "test-api-key")
    monkeypatch.setattr(settings, "nexhealth_base_url", "https://nexhealth.test")
    monkeypatch.setattr(settings, "nexhealth_max_keepalive_connections", 1)
    monkeypatch.setattr(settings, "nexhealth_max_connections", 1)

    async def fake_request(client, method, path, params=None, json=None):
        if path == "/webhook_endpoints":
            return {"data": {"id": "endpoint-1", "secret_key": "shadow-secret"}}
        return {"data": {"id": "sub-1"}}

    class FakeNexHealthClient:
        def __init__(self, config):
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    row = NexHealthWebhookShadowSubscription(
        institution_id="11111111-1111-1111-1111-111111111111",
        location_id="22222222-2222-2222-2222-222222222222",
        route_family=SHADOW_ROUTE_APPOINTMENTS,
        subdomain="demo-subdomain",
        nexhealth_location_id="nexloc-1",
        event_types=["appointment_updated"],
    )
    service = NexHealthWebhookShadowSubscriptionService(AsyncMock())

    with (
        patch("src.app.api.helpers.handle_nexhealth_request", new=fake_request),
        patch(
            "src.app.nexhealth.client.NexHealthClient",
            new=FakeNexHealthClient,
        ),
    ):
        await service._try_remote_create(
            row=row,
            institution=SimpleNamespace(id="inst-1"),
            location=_location(),
            callback_url="https://api.example.com/api/v1/nexhealth/webhooks/shadow/appointments",
            event_types=["appointment_updated"],
        )

    assert row.provider_endpoint_id == "endpoint-1"
    assert row.provider_subscription_ids == ["sub-1"]
    assert row.secret_key == "shadow-secret"
    assert row.secret_key_encrypted != "shadow-secret"
    assert row.status == NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value


def test_shadow_callback_url_uses_distinct_shadow_routes():
    assert (
        shadow_callback_url("https://api.example.com/", SHADOW_ROUTE_SYNC_STATUS)
        == "https://api.example.com/api/v1/nexhealth/webhooks/shadow/sync-status"
    )


def test_shadow_signature_helper_accepts_current_hmac_shape():
    raw_body = b'{"event_name":"appointment_created"}'
    assert _sign(raw_body, "1700000000", "testsecret")
