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
    _gotracker_status_label,
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


def test_gotracker_status_id_labels_match_tracker_dropdown_order():
    assert _gotracker_status_label("1") == "booked"
    assert _gotracker_status_label("2") == "booked_waiting"
    assert _gotracker_status_label("3") == "cancelled"
    assert _gotracker_status_label("4") == "late"
    assert _gotracker_status_label("5") == "no_show"
    assert _gotracker_status_label("6") == "office_cancel"
    assert _gotracker_status_label("7") == "pending"
    assert _gotracker_status_label("8") == "short_cancel"
    assert _gotracker_status_label("9") == "waiting"


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
        "src.app.tasks.automation_workflow.trigger_appointment_state_workflows"
    ) as state_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as reactivation_task:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        trigger_task.delay = MagicMock()
        state_task.delay = MagicMock()
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
    state_task.delay.assert_not_called()
    reactivation_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_appointment_created_upserts_embedded_patient_when_contact_missing():
    payload = {
        "event": "appointment.created",
        "data": {
            "appointment": {
                "AppointmentId": 55,
                "ContactId": 42,
                "StartTime": "2026-08-01T10:00:00Z",
                "ProviderId": 7,
                "AppointmentTypeId": 9,
                "Patient": {
                    "ContactId": 42,
                    "FirstName": "Ava",
                    "LastName": "Jones",
                    "Email": "ava@example.com",
                    "Phone": "+15551234567",
                },
            }
        },
    }
    request = _make_request(payload)
    projection, projection_patch = _patch_projection(change="new", contact_id="contact-from-patient")
    lifecycle, lifecycle_patch = _patch_subscription_lifecycle()

    with patch("src.app.api.routes.gotracker_webhooks.settings") as mock_settings, patch(
        "src.app.api.routes.gotracker_webhooks.get_system_db_session",
        side_effect=[
            _session_with_scalar(_location()),
            _session_with_scalar(None),
            _processing_session(),
        ],
    ), patch("src.app.api.routes.gotracker_webhooks._claim_event", new=AsyncMock(return_value=True)), patch(
        "src.app.api.routes.gotracker_webhooks._complete_event", new=AsyncMock()
    ), projection_patch, lifecycle_patch, patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as trigger_task, patch(
        "src.app.tasks.automation_workflow.trigger_appointment_state_workflows"
    ) as state_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as reactivation_task:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        trigger_task.delay = MagicMock()
        state_task.delay = MagicMock()
        reactivation_task.delay = MagicMock()
        result = await gotracker_webhook("loc-1", request)

    assert result["status"] == "queued"
    projection.upsert_patient.assert_awaited_once()
    patient_kwargs = projection.upsert_patient.await_args.kwargs
    assert patient_kwargs["patient"]["id"] == "gt-42"
    assert patient_kwargs["patient"]["first_name"] == "Ava"
    projection.upsert_appointment.assert_awaited_once()
    appointment_kwargs = projection.upsert_appointment.await_args.kwargs
    assert appointment_kwargs["contact_id"] == "contact-from-patient"
    trigger_task.delay.assert_called_once()
    assert trigger_task.delay.call_args.kwargs["contact_id"] == "contact-from-patient"
    state_task.delay.assert_not_called()
    reactivation_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_appointment_created_accepts_tracker_date_and_time_fields():
    payload = {
        "event": "appointment.created",
        "data": {
            "appointment": {
                "AppointmentId": 900000004,
                "ContactId": 900000001,
                "AppointmentDate": "2026-07-28T00:00:00.000Z",
                "AppointmentTime": "10:00:00",
                "ProviderId": 2,
                "ScheduleColumnId": 1,
                "IsPreconfirmed": False,
                "IsConfirmed": False,
                "MasterId": None,
                "Duration": "00:15:00",
                "OriginalDate": "2026-07-28T00:00:00.000Z",
                "Reason": "bridge prep",
                "Detail": None,
                "AppointmentAmount": 0.0,
                "IsRecall": False,
                "IsPersonal": False,
                "IsAllDayAppointment": False,
                "HasAlarm": False,
                "NotifyTime": None,
                "StatusId": 1,
                "CheckIn": None,
                "InChair": None,
                "OutChair": None,
                "CheckOut": None,
                "FlowState": None,
                "FlowChange": None,
                "Comments": None,
                "BookedUserId": "Admin",
                "BookedTimeStamp": "2026-07-29T20:32:00.81",
                "BookedMachineName": "EC2AMAZ-QKGJ1Q1",
                "CreatedUserId": "Admin",
                "CreatedTimeStamp": "2026-07-29T20:32:00.807",
                "ModifiedUserId": "Admin",
                "ModifiedTimeStamp": "2026-07-29T20:32:00.807",
                "ModifiedMachineName": "EC2AMAZ-QKGJ1Q1",
                "CreatedMachineName": "EC2AMAZ-QKGJ1Q1",
                "RebookInfo": None,
                "ConfirmedTimeStamp": None,
                "ConfirmedUserId": None,
                "ConfirmedMachineName": None,
                "RebookId": None,
                "CancelledTimeStamp": None,
                "CancelledUserId": None,
                "CancelledMachineName": None,
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
        "src.app.tasks.automation_workflow.trigger_appointment_state_workflows"
    ) as state_task, patch(
        "src.app.tasks.automation_workflow.resume_reactivation_booking"
    ) as reactivation_task:
        mock_settings.gotracker_webhook_secret = ""
        mock_settings.is_production = False
        trigger_task.delay = MagicMock()
        state_task.delay = MagicMock()
        reactivation_task.delay = MagicMock()
        result = await gotracker_webhook("loc-1", request)

    assert result["status"] == "queued"
    projection.upsert_appointment.assert_awaited_once()
    upsert_kwargs = projection.upsert_appointment.await_args.kwargs
    assert upsert_kwargs["appointment_id"] == "gt-900000004"
    assert upsert_kwargs["start_time"] == "2026-07-28T10:00:00Z"
    assert upsert_kwargs["gotracker_status_id"] == 1
    assert upsert_kwargs["is_confirmed"] is False
    assert upsert_kwargs["is_preconfirmed"] is False
    assert upsert_kwargs["status_source"] == "webhook"
    trigger_task.delay.assert_called_once()
    metadata = trigger_task.delay.call_args.kwargs["trigger_metadata"]
    assert metadata["appointment_reason"] == "bridge prep"
    assert metadata["appointment_reasons"] == ["bridge prep"]
    assert metadata["gotracker_contact_id"] == "900000001"
    assert metadata["contact_source_id"] == "gt-900000001"
    assert metadata["provider_id"] == "gt-2"
    assert metadata["gotracker_provider_id"] == "2"
    assert metadata["schedule_column_id"] == "1"
    assert metadata["appointment_status"] == "booked"
    assert metadata["appointment_status_id"] == "1"
    assert metadata["appointment_date"] == "2026-07-28T00:00:00.000Z"
    assert metadata["appointment_time"] == "10:00:00"
    assert metadata["appointment_datetime"] == "2026-07-28T10:00:00Z"
    state_task.delay.assert_called_once()
    state_kwargs = state_task.delay.call_args.kwargs
    assert state_kwargs["appointment_id"] == "gt-900000004"
    assert state_kwargs["status_id"] == 1
    assert state_kwargs["confirmed"] is False
    assert state_kwargs["preconfirmed"] is False
    assert metadata["appointment_duration"] == "00:15:00"
    assert metadata["is_preconfirmed"] is False
    assert metadata["is_confirmed"] is False
    assert metadata["original_date"] == "2026-07-28T00:00:00.000Z"
    assert metadata["appointment_amount"] == 0.0
    assert metadata["booked_machine_name"] == "EC2AMAZ-QKGJ1Q1"
    assert metadata["created_user_id"] == "Admin"
    assert metadata["modified_machine_name"] == "EC2AMAZ-QKGJ1Q1"
    assert metadata["booked_user_id"] == "Admin"
    assert metadata["booked_timestamp"] == "2026-07-29T20:32:00.81"
    assert metadata["created_machine_name"] == "EC2AMAZ-QKGJ1Q1"
    assert metadata["gotracker_payload"]["appointment"]["contact_id"] == "900000001"
    assert metadata["gotracker_payload"]["appointment"]["date"] == "2026-07-28T00:00:00.000Z"
    assert metadata["gotracker_payload"]["appointment"]["time"] == "10:00:00"
    assert metadata["gotracker_payload"]["appointment"]["datetime"] == "2026-07-28T10:00:00Z"
    assert metadata["gotracker_payload"]["appointment"]["status_id"] == "1"
    assert metadata["gotracker_payload"]["appointment"]["is_confirmed"] is False
    assert metadata["gotracker_payload"]["appointment"]["booked_machine_name"] == "EC2AMAZ-QKGJ1Q1"
    assert metadata["gotracker_payload"]["data"]["AppointmentId"] == 900000004
    assert metadata["gotracker_payload"]["data"]["AppointmentDate"] == "2026-07-28T00:00:00.000Z"
    assert metadata["gotracker_payload"]["data"]["AppointmentTime"] == "10:00:00"
    assert metadata["gotracker_payload"]["data"]["Reason"] == "bridge prep"


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
