"""Unit tests for the NexHealth appointment webhook receiver."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.nexhealth_webhooks import (
    _verify_signature,
    nexhealth_appointment_webhook,
    nexhealth_patient_webhook,
    nexhealth_sync_status_webhook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, timestamp: str, secret: str = "testsecret") -> str:
    """Build a signature the same way the handler verifies it: HMAC-SHA256 over
    ``{timestamp}.{base64(raw_body)}`` with the endpoint secret, hex digest."""
    signed = f"{timestamp}.{base64.b64encode(body).decode('ascii')}"
    return hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()


def _make_request(
    payload: dict,
    signature: str | None = None,
    timestamp: str | None = None,
    raw_body: bytes | None = None,
):
    body = raw_body if raw_body is not None else json.dumps(payload).encode()
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.json = AsyncMock(return_value=payload)
    headers: dict[str, str] = {}
    if signature:
        headers["signature"] = signature
    if timestamp:
        headers["timestamp"] = timestamp
    request.headers = headers
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


def _patch_projection(change="new"):
    """Patch NexHealthProjectionService (imported at call-time) with a mock whose
    claim_event→True, upsert_appointment→change, complete_event→noop."""
    from types import SimpleNamespace

    inst = MagicMock()
    inst.claim_event = AsyncMock(return_value=True)
    inst.upsert_appointment = AsyncMock(return_value=SimpleNamespace(change=change))
    inst.complete_event = AsyncMock()
    return patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService",
        return_value=inst,
    )


def _patch_failing_projection():
    from types import SimpleNamespace

    inst = MagicMock()
    inst.claim_event = AsyncMock(return_value=True)
    inst.upsert_appointment = AsyncMock(side_effect=RuntimeError("projection failed"))
    inst.complete_event = AsyncMock(return_value=SimpleNamespace(attempts=2))
    return patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService",
        return_value=inst,
    ), inst


def _patch_patient_projection(change="updated", contact_id="contact-patient-1"):
    from types import SimpleNamespace

    inst = MagicMock()
    inst.claim_event = AsyncMock(return_value=True)
    inst.upsert_patient = AsyncMock(
        return_value=SimpleNamespace(contact=SimpleNamespace(id=contact_id), change=change)
    )
    inst.complete_event = AsyncMock()
    return patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService",
        return_value=inst,
    )


def _patch_subscription_lifecycle():
    inst = MagicMock()
    inst.record_event_seen = AsyncMock()
    return patch(
        "src.app.services.automation.nexhealth_subscription_service.NexHealthSubscriptionLifecycleService",
        return_value=inst,
    )


def _patch_sync_status_service(location, updated=1):
    inst = MagicMock()
    inst.resolve_locations_for_payload = AsyncMock(return_value=[location])
    inst.upsert_for_locations = AsyncMock(return_value=updated)
    return patch(
        "src.app.services.automation.nexhealth_sync_status_service.NexHealthSyncStatusService",
        return_value=inst,
    )


def _make_cm_session():
    """A bare async-context-manager session (for the projection block)."""
    s = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    s.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    s.execute = AsyncMock(return_value=result)
    return s


_VALID_PAYLOAD = {
    "event_name": "appointment_insertion.complete",
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
        mock_settings.is_production = False
        # Should not raise even with no headers
        _verify_signature(b"body", None, None)


def test_verify_signature_rejects_in_production_without_secret():
    """Defense-in-depth: even if startup validation is bypassed, an unset
    secret in production must reject rather than accept an unauthenticated,
    potentially cross-tenant webhook (P0-1)."""
    from fastapi import HTTPException

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = True
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", None, None)
    assert exc.value.status_code == 403


def test_verify_signature_raises_403_missing_header():
    from fastapi import HTTPException

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", None, None)
    assert exc.value.status_code == 403


def test_verify_signature_raises_403_wrong_signature():
    from fastapi import HTTPException

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        with pytest.raises(HTTPException) as exc:
            _verify_signature(b"body", "badhex", "1700000000")
    assert exc.value.status_code == 403


def test_verify_signature_passes_correct_signature():
    body = b'{"event_name":"test"}'
    timestamp = "1700000000"
    sig = _sign(body, timestamp, "s3cr3t")
    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = "s3cr3t"
        _verify_signature(body, sig, timestamp)  # should not raise


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
        side_effect=[mock_session, _make_cm_session()],
    ), _patch_projection(change="new"), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as mock_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as mock_reactivation:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        mock_task.delay = MagicMock()
        mock_reactivation.delay = MagicMock()
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "queued"
    assert result["appointment_id"] == "appt-999"
    mock_task.delay.assert_called_once()
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["institution_id"] == "inst-1"
    assert kwargs["appointment_id"] == "appt-999"
    assert kwargs["contact_id"] == "contact-1"
    assert kwargs["appointment_at_iso"] == "2026-08-01T10:00:00Z"
    mock_reactivation.delay.assert_called_once_with(
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        appointment_id="appt-999",
    )


@pytest.mark.asyncio
async def test_webhook_queues_task_with_no_contact():
    location = _make_location()
    mock_session = _make_session(location=location, contact=None)
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[mock_session, _make_cm_session()],
    ), _patch_projection(change="new"), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as mock_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as mock_reactivation:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        mock_task.delay = MagicMock()
        mock_reactivation.delay = MagicMock()
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "queued"
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["contact_id"] is None
    mock_reactivation.delay.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_queues_task_for_appointment_created_plural_payload():
    """NexHealth documents appointment_created as data.appointments[]."""
    location = _make_location()
    contact = MagicMock()
    contact.id = "contact-1"
    mock_session = _make_session(location=location, contact=contact)
    payload = {
        "event_name": "appointment_created",
        "data": {
            "appointments": [
                {
                    "id": "appt-created-1",
                    "location_id": "nexloc-1",
                    "patient_id": "nexpat-42",
                    "provider_id": "prov-1",
                    "appointment_type_id": "type-1",
                    "start_time": "2026-08-02T10:00:00Z",
                }
            ]
        },
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[mock_session, _make_cm_session()],
    ), _patch_projection(change="new"), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as mock_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as mock_reactivation:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        mock_task.delay = MagicMock()
        mock_reactivation.delay = MagicMock()
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "queued"
    assert result["appointment_id"] == "appt-created-1"
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["appointment_id"] == "appt-created-1"
    assert kwargs["trigger_metadata"]["event"] == "appointment_created"
    assert kwargs["trigger_metadata"]["nexhealth_location_id"] == "nexloc-1"


# ---------------------------------------------------------------------------
# nexhealth_appointment_webhook — ignored cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_ignores_unhandled_event():
    payload = {**_VALID_PAYLOAD, "event_name": "form_request_completed"}
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "ignored"
    assert result["event"] == "form_request_completed"


@pytest.mark.asyncio
async def test_patient_event_refreshes_contact_projection_on_existing_appointment_url():
    """Current NexHealth endpoint target can send patient events to the same receiver URL."""
    location = _make_location()
    lookup_session = AsyncMock()
    lookup_session.__aenter__ = AsyncMock(return_value=lookup_session)
    lookup_session.__aexit__ = AsyncMock(return_value=False)
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    lookup_session.execute = AsyncMock(return_value=loc_result)

    webhook_session = _make_cm_session()
    payload = {
        "event_name": "patient_updated",
        "subdomain": "silora-demo-practice",
        "event_time": "2026-07-20T19:13:24Z",
        "data": {
            "patient": {
                "id": "pat-1",
                "first_name": "Sam",
                "last_name": "Lee",
                "email": "sam@example.com",
                "location_ids": ["nexloc-1"],
                "bio": {"phone_number": "+15551234567", "date_of_birth": "1990-01-01"},
            }
        },
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[lookup_session, webhook_session],
    ), _patch_patient_projection(change="updated"), _patch_subscription_lifecycle():
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "processed"
    assert result["event"] == "patient_updated"
    assert result["processed"] == 1
    assert result["results"][0]["patient_id"] == "pat-1"
    assert result["results"][0]["contact_id"] == "contact-patient-1"
    webhook_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_patient_webhook_route_ignores_appointment_event():
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_patient_webhook(request)

    assert result == {"status": "ignored", "event": "appointment_insertion.complete"}


@pytest.mark.asyncio
async def test_sync_status_event_processes_on_existing_appointment_url():
    """Current NexHealth endpoint target can send sync-status events to the same receiver URL."""
    location = _make_location()
    lookup_session = _make_cm_session()
    webhook_session = _make_cm_session()
    payload = {
        "event_name": "sync_status_read_change",
        "subdomain": "silora-demo-practice",
        "event_time": "2026-07-21T10:00:00Z",
        "data": {
            "read_status": "green",
            "read_status_at": "2026-07-21T10:00:00Z",
            "write_status": "green",
            "write_status_at": "2026-07-21T10:00:00Z",
            "locations": [{"id": "nexloc-1"}],
        },
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[lookup_session, webhook_session],
    ), _patch_sync_status_service(location), _patch_projection(), _patch_subscription_lifecycle():
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "processed"
    assert result["event"] == "sync_status_read_change"
    assert result["processed"] == 1
    assert result["location_ids"] == ["loc-1"]
    webhook_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_status_route_ignores_patient_event():
    payload = {
        "event_name": "patient_updated",
        "subdomain": "silora-demo-practice",
        "data": {"patient": {"id": "pat-1"}},
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_sync_status_webhook(request)

    assert result == {"status": "ignored", "event": "patient_updated"}


@pytest.mark.asyncio
async def test_webhook_cancels_runs_on_cancelled_update():
    """An appointment.updated carrying cancelled=True cancels scheduled runs/timers."""
    location = _make_location()
    # location lookup only (contact lookup skipped on cancellation path)
    lookup_session = AsyncMock()
    lookup_session.__aenter__ = AsyncMock(return_value=lookup_session)
    lookup_session.__aexit__ = AsyncMock(return_value=False)
    loc_result = MagicMock()
    loc_result.scalar_one_or_none.return_value = location
    lookup_session.execute = AsyncMock(return_value=loc_result)

    # cancellation session: returns one active run for the appointment
    cancel_session = AsyncMock()
    cancel_session.__aenter__ = AsyncMock(return_value=cancel_session)
    cancel_session.__aexit__ = AsyncMock(return_value=False)
    cancel_session.commit = AsyncMock()
    run = MagicMock()
    run.id = "run-1"
    runs_result = MagicMock()
    runs_result.scalars.return_value.all.return_value = [run]
    cancel_session.execute = AsyncMock(return_value=runs_result)

    payload = {
        "event_name": "appointment_updated.complete",
        "data": {
            "appointment": {
                "id": "appt-999",
                "location_id": "nexloc-1",
                "patient_id": "nexpat-42",
                "start_time": "2026-08-01T10:00:00Z",
                "cancelled": True,
            }
        },
    }
    request = _make_request(payload)

    mock_enroll_svc = AsyncMock()
    mock_scheduler = AsyncMock()

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[lookup_session, _make_cm_session(), cancel_session],
    ), _patch_projection(change="cancelled"), patch(
        "src.app.services.automation.enrollment_service.AutomationWorkflowEnrollmentService",
        return_value=mock_enroll_svc,
    ), patch(
        "src.app.services.automation.scheduler_service.AutomationWorkflowSchedulerService",
        return_value=mock_scheduler,
    ):
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "cancelled"
    assert result["runs_cancelled"] == 1
    mock_scheduler.cancel_timers_for_run.assert_awaited_once_with("run-1")
    mock_enroll_svc.cancel_run.assert_awaited_once()
    cancel_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_location():
    mock_session = _make_session(location=None)
    request = _make_request(_VALID_PAYLOAD)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        return_value=mock_session,
    ):
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "ignored"
    assert result["reason"] == "unknown_location"


@pytest.mark.asyncio
async def test_claimed_webhook_failure_is_dead_lettered_and_acknowledged():
    location = _make_location()
    contact = MagicMock()
    contact.id = "contact-1"
    lookup_session = _make_session(location=location, contact=contact)
    webhook_session = _make_cm_session()
    request = _make_request(_VALID_PAYLOAD)
    projection_patch, projection = _patch_failing_projection()
    capture_dead_letter = AsyncMock()

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.nexhealth_webhooks.get_system_db_session",
        side_effect=[lookup_session, webhook_session],
    ), projection_patch, _patch_subscription_lifecycle(), patch(
        "src.app.api.routes.nexhealth_webhooks.capture_dead_letter",
        new=capture_dead_letter,
    ):
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
        result = await nexhealth_appointment_webhook(request)

    assert result["status"] == "failed"
    assert result["dead_lettered"] is True
    projection.complete_event.assert_awaited_once()
    assert projection.complete_event.call_args.kwargs["error"] == "projection failed"
    capture_dead_letter.assert_awaited_once()
    assert capture_dead_letter.call_args.kwargs["source"] == "nexhealth_webhook"
    assert capture_dead_letter.call_args.kwargs["attempts"] == 2
    webhook_session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# nexhealth_appointment_webhook — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_rejects_missing_start_time():
    from fastapi import HTTPException

    payload = {
        "event_name": "appointment_insertion.complete",
        "data": {
            "appointment": {"id": "appt-1", "location_id": "nexloc-1"}
            # start_time missing
        },
    }
    request = _make_request(payload)

    with patch("src.app.api.routes.nexhealth_webhooks.settings") as mock_settings:
        mock_settings.nexhealth_webhook_secret = ""
        mock_settings.is_production = False
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
        mock_settings.is_production = False
        with pytest.raises(HTTPException) as exc:
            await nexhealth_appointment_webhook(request)

    assert exc.value.status_code == 400
