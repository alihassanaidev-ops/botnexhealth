"""Unit tests for Plan 09 appointment backfill/reconciliation service."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.nexhealth_backfill_service import (
    NexHealthAppointmentSyncService,
    NexHealthPatientSyncService,
)


def _result(*, first=None, scalar=None):
    result = MagicMock()
    result.first.return_value = first
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = []
    return result


@pytest.mark.asyncio
async def test_backfill_projects_new_appointment_and_triggers_workflow():
    subscription = SimpleNamespace(
        id="sub-1",
        institution_id="inst-1",
        status=NexHealthWebhookSubscriptionStatus.PENDING.value,
        last_backfill_at=None,
        updated_at=None,
        error_metadata={"old": "error"},
    )
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(id="loc-1", nexhealth_subdomain="sub", nexhealth_location_id="nh-loc")

    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _result(first=(subscription, institution, location)),
            _result(scalar=SimpleNamespace(id="contact-1")),  # contact lookup
            _result(scalar=None),  # projection row lookup
        ]
    )

    adapter = AsyncMock()
    adapter.list_appointments = AsyncMock(
        return_value=[
            {
                "id": "appt-1",
                "patient_id": "pat-1",
                "location_id": "nh-loc",
                "start_time": "2026-08-01T10:00:00Z",
                "cancelled": False,
            }
        ]
    )
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.services.automation.nexhealth_backfill_service._trigger_appointment_workflows"
    ) as trigger:
        summary = await NexHealthAppointmentSyncService(session).sync_subscription(
            subscription_id="sub-1",
            mode="backfill",
        )

    assert summary.locations_scanned == 1
    assert summary.appointments_seen == 1
    assert summary.projected == 1
    assert summary.triggered == 1
    assert subscription.last_backfill_at is not None
    assert subscription.error_metadata is None
    trigger.assert_called_once()
    assert trigger.call_args.kwargs["appointment_id"] == "appt-1"
    assert trigger.call_args.kwargs["contact_id"] == "contact-1"


@pytest.mark.asyncio
async def test_reconciliation_cancels_runs_for_cancelled_appointment():
    subscription = SimpleNamespace(
        id="sub-1",
        institution_id="inst-1",
        status=NexHealthWebhookSubscriptionStatus.ACTIVE.value,
        last_reconciliation_at=None,
        updated_at=None,
        error_metadata=None,
    )
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(id="loc-1", nexhealth_subdomain="sub", nexhealth_location_id="nh-loc")

    existing_projection = SimpleNamespace(
        start_time=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
        location_id="loc-1",
        nexhealth_patient_id="pat-1",
        contact_id="contact-1",
        last_event=None,
        last_synced_at=None,
        updated_at=None,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _result(first=(subscription, institution, location)),
            _result(scalar=None),  # contact lookup
            _result(scalar=existing_projection),  # projection row lookup
        ]
    )

    adapter = AsyncMock()
    adapter.list_appointments = AsyncMock(
        return_value=[
            {
                "id": "appt-1",
                "patient_id": "pat-1",
                "location_id": "nh-loc",
                "start_time": "2026-08-01T10:00:00Z",
                "cancelled": True,
            }
        ]
    )
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.services.automation.nexhealth_backfill_service._cancel_runs_for_appointment",
        AsyncMock(return_value=2),
    ) as cancel, patch(
        "src.app.services.automation.nexhealth_backfill_service._trigger_appointment_workflows"
    ) as trigger:
        summary = await NexHealthAppointmentSyncService(session).sync_subscription(
            subscription_id="sub-1",
            mode="reconciliation",
        )

    assert summary.projected == 1
    assert summary.triggered == 0
    assert summary.cancelled_runs == 2
    assert subscription.last_reconciliation_at is not None
    cancel.assert_awaited_once()
    trigger.assert_not_called()


@pytest.mark.asyncio
async def test_patient_backfill_projects_contact_and_patient_working_set():
    subscription = SimpleNamespace(
        id="sub-1",
        institution_id="inst-1",
        status=NexHealthWebhookSubscriptionStatus.PENDING.value,
        last_patient_backfill_at=None,
        updated_at=None,
        error_metadata={"old": "error"},
    )
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(
        id="loc-1",
        institution_id="inst-1",
        nexhealth_subdomain="sub",
        nexhealth_location_id="nh-loc",
    )

    loc_result = _result()
    loc_result.scalars.return_value.all.return_value = [location]
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result(first=(subscription, institution, location)),
            loc_result,
        ]
    )

    adapter = AsyncMock()
    adapter.list_patients = AsyncMock(
        return_value=[
            {
                "id": "pat-1",
                "first_name": "Sam",
                "last_name": "Lee",
                "location_ids": ["nh-loc"],
                "bio": {"phone_number": "+15551234567"},
            }
        ]
    )
    adapter.close = AsyncMock()
    projection = MagicMock()
    projection.upsert_patient = AsyncMock()

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.services.automation.nexhealth_backfill_service.NexHealthProjectionService",
        return_value=projection,
    ):
        summary = await NexHealthPatientSyncService(session).sync_subscription(
            subscription_id="sub-1",
            mode="backfill",
        )

    assert summary.locations_scanned == 1
    assert summary.patients_seen == 1
    assert summary.projected == 1
    assert subscription.last_patient_backfill_at is not None
    assert subscription.error_metadata is None
    adapter.list_patients.assert_awaited_once_with(updated_since=None)
    projection.upsert_patient.assert_awaited_once()
    assert projection.upsert_patient.call_args.kwargs["local_location_ids"] == ["loc-1"]
    assert projection.upsert_patient.call_args.kwargs["nexhealth_location_ids"] == ["nh-loc"]
    assert projection.upsert_patient.call_args.kwargs["event"] == "patient.backfill"


@pytest.mark.asyncio
async def test_patient_reconciliation_uses_patient_watermark_overlap():
    watermark = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    subscription = SimpleNamespace(
        id="sub-1",
        institution_id="inst-1",
        status=NexHealthWebhookSubscriptionStatus.ACTIVE.value,
        last_patient_backfill_at=watermark,
        last_patient_reconciliation_at=None,
        updated_at=None,
        error_metadata=None,
    )
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(id="loc-1", nexhealth_subdomain="sub", nexhealth_location_id="nh-loc")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(first=(subscription, institution, location)))

    adapter = AsyncMock()
    adapter.list_patients = AsyncMock(return_value=[])
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ):
        summary = await NexHealthPatientSyncService(session).sync_subscription(
            subscription_id="sub-1",
            mode="reconciliation",
        )

    assert summary.locations_scanned == 1
    assert subscription.last_patient_reconciliation_at is not None
    adapter.list_patients.assert_awaited_once_with(
        updated_since="2026-07-21T09:00:00+00:00"
    )
