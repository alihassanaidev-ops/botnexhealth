"""Unit tests for the GoTracker webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.routes.gotracker_webhooks import (
    _verify_signature,
    gotracker_webhook,
)


def _sign(body: bytes, timestamp: str, secret: str = "testsecret") -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _make_request(payload: dict, signature: str | None = None):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.json = AsyncMock(return_value=payload)
    request.headers = {"X-ScaleNexus-Signature": signature} if signature else {}
    return request


def _location():
    return SimpleNamespace(id="loc-1", institution_id="inst-1")


def _session_with_scalar(value):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    session.execute = AsyncMock(return_value=result)
    return session


def _processing_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session


def _patch_projection(change="new", contact_id="contact-1"):
    inst = MagicMock()
    inst.upsert_appointment = AsyncMock(return_value=SimpleNamespace(change=change))
    inst.upsert_patient = AsyncMock(
        return_value=SimpleNamespace(
            change=change,
            contact=SimpleNamespace(id=contact_id),
        )
    )
    return inst, patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService",
        return_value=inst,
    )


def _patch_subscription_lifecycle():
    lifecycle = MagicMock()
    lifecycle.record_event_seen = AsyncMock()
    return lifecycle, patch(
        "src.app.services.automation.gotracker_subscription_service."
        "GoTrackerSubscriptionLifecycleService",
        return_value=lifecycle,
    )


def test_verify_signature_skips_without_secret_outside_production():
    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        _verify_signature(b"body", None)


def test_verify_signature_rejects_missing_secret_in_production():
    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = True
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", None)
    assert exc.value.status_code == 403


def test_verify_signature_rejects_stale_timestamp():
    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings:
        mock_settings.gotracker_webhook_secret = "testsecret"
        mock_settings.is_production = False
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", _sign(b"body", "1700000000"))
    assert exc.value.status_code == 403


def test_verify_signature_accepts_current_signature():
    body = b'{"event":"patient.created"}'
    timestamp = str(int(time.time()))
    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings:
        mock_settings.gotracker_webhook_secret = "testsecret"
        mock_settings.is_production = False
        _verify_signature(body, _sign(body, timestamp))


@pytest.mark.asyncio
async def test_appointment_created_updates_projection_and_queues_workflow():
    payload = {
        "event": "appointment.created",
        "data": {
            "appointment": {
                "AppointmentId": 55,
                "ContactId": 42,
                "StartTime": "2026-08-01T10:00:00Z",
                "ProviderId": 7,
                "AppointmentTypeId": 9,
            }
        },
    }
    request = _make_request(payload)
    projection, projection_patch = _patch_projection(change="new")
    lifecycle, lifecycle_patch = _patch_subscription_lifecycle()

    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.gotracker_webhooks.get_system_db_session",
        side_effect=[
            _session_with_scalar(_location()),
            _session_with_scalar(SimpleNamespace(id="contact-1")),
            _processing_session(),
        ],
    ), patch("src.app.api.routes.gotracker_webhooks._claim_event", new=AsyncMock(return_value=True)), patch(
        "src.app.api.routes.gotracker_webhooks._complete_event", new=AsyncMock()
    ), projection_patch, lifecycle_patch, patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as trigger_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as reactivation_task:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        trigger_task.delay = MagicMock()
        reactivation_task.delay = MagicMock()
        result = await gotracker_webhook("loc-1", request)

    assert result["status"] == "queued"
    projection.upsert_appointment.assert_awaited_once()
    upsert_kwargs = projection.upsert_appointment.await_args.kwargs
    assert upsert_kwargs["appointment_id"] == "gt-55"
    assert upsert_kwargs["nexhealth_patient_id"] == "gt-42"
    assert upsert_kwargs["provider_id"] == "gt-7"
    assert upsert_kwargs["appointment_type_id"] == "gt-9"
    lifecycle.record_event_seen.assert_awaited_once_with(
        institution_id="inst-1",
        location_id="loc-1",
    )
    trigger_task.delay.assert_called_once()
    assert trigger_task.delay.call_args.kwargs["appointment_id"] == "gt-55"
    reactivation_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_appointment_cancelled_cancels_existing_runs():
    payload = {
        "event": "appointment.cancelled",
        "data": {"appointment": {"id": "abc", "patient_id": "pat"}},
    }
    request = _make_request(payload)
    projection, projection_patch = _patch_projection(change="cancelled")
    lifecycle, lifecycle_patch = _patch_subscription_lifecycle()

    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.gotracker_webhooks.get_system_db_session",
        side_effect=[
            _session_with_scalar(_location()),
            _session_with_scalar(SimpleNamespace(id="contact-1")),
            _processing_session(),
        ],
    ), patch("src.app.api.routes.gotracker_webhooks._claim_event", new=AsyncMock(return_value=True)), patch(
        "src.app.api.routes.gotracker_webhooks._complete_event", new=AsyncMock()
    ), projection_patch, lifecycle_patch, patch(
        "src.app.api.routes.nexhealth_webhooks._cancel_runs_for_appointment",
        new=AsyncMock(return_value=2),
    ):
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        result = await gotracker_webhook("loc-1", request)

    assert result["results"][0]["status"] == "cancelled"
    assert result["results"][0]["runs_cancelled"] == 2
    projection.upsert_appointment.assert_awaited_once()
    lifecycle.record_event_seen.assert_awaited_once_with(
        institution_id="inst-1",
        location_id="loc-1",
    )


@pytest.mark.asyncio
async def test_patient_created_updates_projection():
    payload = {
        "event": "patient.created",
        "data": {
            "patient": {
                "ContactId": 42,
                "FirstName": "Ava",
                "LastName": "Jones",
                "Email": "ava@example.com",
                "Phone": "+15551234567",
            }
        },
    }
    request = _make_request(payload)
    projection, projection_patch = _patch_projection(change="new")
    lifecycle, lifecycle_patch = _patch_subscription_lifecycle()

    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.gotracker_webhooks.get_system_db_session",
        side_effect=[_session_with_scalar(_location()), _processing_session()],
    ), patch("src.app.api.routes.gotracker_webhooks._claim_event", new=AsyncMock(return_value=True)), patch(
        "src.app.api.routes.gotracker_webhooks._complete_event", new=AsyncMock()
    ), projection_patch, lifecycle_patch:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        result = await gotracker_webhook("loc-1", request)

    assert result["status"] == "processed"
    projection.upsert_patient.assert_awaited_once()
    lifecycle.record_event_seen.assert_awaited_once_with(
        institution_id="inst-1",
        location_id="loc-1",
    )
    patient_payload = projection.upsert_patient.await_args.kwargs["patient"]
    assert patient_payload["id"] == "gt-42"
    assert patient_payload["first_name"] == "Ava"
    assert patient_payload["bio"]["phone_number"] == "+15551234567"


@pytest.mark.asyncio
async def test_duplicate_claim_returns_duplicate_without_projection():
    payload = {
        "event": "patient.updated",
        "data": {"patient": {"ContactId": 42}},
    }
    request = _make_request(payload)
    projection, projection_patch = _patch_projection(change="updated")

    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.gotracker_webhooks.get_system_db_session",
        side_effect=[_session_with_scalar(_location()), _processing_session()],
    ), patch("src.app.api.routes.gotracker_webhooks._claim_event", new=AsyncMock(return_value=False)), projection_patch:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        result = await gotracker_webhook("loc-1", request)

    assert result["results"][0]["status"] == "duplicate"
    projection.upsert_patient.assert_not_awaited()
