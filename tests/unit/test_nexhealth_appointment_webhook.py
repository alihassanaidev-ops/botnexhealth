"""Unit tests for the NexHealth appointment webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.nexhealth_webhooks import (
    _verify_signature,
    nexhealth_appointment_webhook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = "testsecret") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_request(payload: dict, signature: str | None = None, raw_body: bytes | None = None):
    body = raw_body if raw_body is not None else json.dumps(payload).encode()
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.json = AsyncMock(return_value=payload)
    request.headers = {"X-NexHealth-Signature": signature} if signature else {}
    return request


def _make_session(location=None, contact=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    loc_result = MagicMock()
    loc_result.scalar_one_or_none.return_value = location
    contact_result = MagicMock()
    contact_result.scalar_one_or_none.return_value = contact

    # First execute → location lookup; second → contact lookup
    session.execute = AsyncMock(side_effect=[loc_result, contact_result])
    return session


def _make_location(institution_id="inst-1", location_id="loc-1"):
    loc = MagicMock()
    loc.institution_id = institution_id
    loc.id = location_id
    return loc


_VALID_PAYLOAD = {
    "event": "appointment.created",
    "data": {
        "appointment": {
            "id": "appt-999",
            "location_id": "nexloc-1",
            "patient_id": "nexpat-42",
            "start_time": "2026-08-01T10:00:00Z",
        }
    },
}


# ---------------------------------------------------------------------------
# _verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_skips_when_no_secret():
    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        # Should not raise even with no header
        _verify_signature(b"body", None)


def test_verify_signature_raises_403_missing_header():
    from fastapi import HTTPException

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", None)
    assert exc.value.status_code == 403


def test_verify_signature_raises_403_wrong_signature():
    from fastapi import HTTPException

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", "badhex")
    assert exc.value.status_code == 403


def test_verify_signature_passes_correct_signature():
    body = b'{"event":"test"}'
    sig = _sign(body, "s3cr3t")
    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        _verify_signature(body, sig)  # should not raise


# ---------------------------------------------------------------------------
# nexhealth_appointment_webhook — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_queues_task_for_created_event():
    location = _make_location()
    contact = MagicMock()
    contact.id = "contact-1"
    mock_session = _make_session(location=location, contact=contact)
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as mock_task:
        mock_settings.nexhealth_webhook_secret = ""
        mock_task.delay = MagicMock()
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "queued"
    assert result["appointment_id"] == "appt-999"
    mock_task.delay.assert_called_once()
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["institution_id"] == "inst-1"
    assert kwargs["appointment_id"] == "appt-999"
    assert kwargs["contact_id"] == "contact-1"
    assert kwargs["appointment_at_iso"] == "2026-08-01T10:00:00Z"


@pytest.mark.asyncio
async def test_webhook_queues_task_with_no_contact():
    location = _make_location()
    mock_session = _make_session(location=location, contact=None)
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as mock_task:
        mock_settings.nexhealth_webhook_secret = ""
        mock_task.delay = MagicMock()
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "queued"
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["contact_id"] is None


# ---------------------------------------------------------------------------
# nexhealth_appointment_webhook — ignored cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_ignores_cancelled_event():
    payload = {**_VALID_PAYLOAD, "event": "appointment.cancelled"}
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "ignored"
    assert result["event"] == "appointment.cancelled"


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_location():
    mock_session = _make_session(location=None)
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        return_value=mock_session,
    ):
        mock_settings.nexhealth_webhook_secret = ""
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "ignored"
    assert result["reason"] == "unknown_location"


# ---------------------------------------------------------------------------
# nexhealth_appointment_webhook — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_rejects_missing_start_time():
    from fastapi import HTTPException

    payload = {
        "event": "appointment.created",
        "data": {
            "appointment": {"id": "appt-1", "location_id": "nexloc-1"}
            # start_time missing
        },
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        with pytest.raises(HTTPException) as exc:
            await nexhealth_appointment_webhook(request)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_webhook_rejects_bad_json():
    from fastapi import HTTPException

    request = _make_request({})
    request.json = AsyncMock(side_effect=Exception("parse error"))

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        with pytest.raises(HTTPException) as exc:
            await nexhealth_appointment_webhook(request)

    assert exc.value.status_code == 400
