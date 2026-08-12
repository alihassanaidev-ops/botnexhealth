"""Unit tests for the NexHealth projection + event-ledger service (Plan 09)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.config import settings
from src.app.models.nexhealth_webhook_event import NexHealthWebhookStatus
from src.app.services.automation.appointment_trigger_service import (
    make_appointment_idempotency_key,
)
from src.app.services.automation.nexhealth_projection_service import (
    NexHealthProjectionService,
)


class _NestedCM:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _session(existing=None):
    session = AsyncMock()
    session.add = MagicMock()
    session.begin_nested = MagicMock(return_value=_NestedCM())
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(return_value=result)
    return session


# ── upsert_appointment change classification ─────────────────────────────────


def _upsert(session, **over):
    svc = NexHealthProjectionService(session)
    kw = dict(
        institution_id="inst-1",
        appointment_id="a-1",
        location_id="loc-1",
        nexhealth_patient_id="p-1",
        contact_id="c-1",
        start_time="2026-08-01T10:00:00Z",
        event="appointment.updated",
        cancelled=False,
    )
    kw.update(over)
    return asyncio.run(svc.upsert_appointment(**kw))


def test_upsert_new_when_no_existing_row():
    res = _upsert(_session(existing=None))
    assert res.change == "new"


def test_upsert_new_stores_gotracker_status_snapshot():
    session = _session(existing=None)
    res = _upsert(
        session,
        gotracker_status_id=5,
        is_confirmed=True,
        is_preconfirmed=False,
        status_source="webhook",
    )
    assert res.change == "new"
    row = session.add.call_args.args[0]
    assert row.gotracker_status_id == 5
    assert row.gotracker_status_label == "no_show"
    assert row.is_confirmed is True
    assert row.is_preconfirmed is False
    assert row.last_status_source == "webhook"
    assert row.last_status_synced_at is not None
    assert row.status == "cancelled"


def test_upsert_stores_gotracker_flow_state_and_marks_flow_change():
    session = _session(existing=None)
    res = _upsert(
        session,
        appointment_reason="Implant Surgery",
        start_time="2026-08-12T10:00:00Z",
        flow_state="Completed",
        flow_changed_at="2026-08-12T09:27:01.940Z",
        checked_in_at="09:00:00",
        checked_out_at="09:27:01",
    )

    row = session.add.call_args.args[0]
    assert res.state_changed is True
    assert row.appointment_reason == "Implant Surgery"
    assert row.flow_state == "Completed"
    assert row.flow_changed_at == datetime(2026, 8, 12, 9, 27, 1, 940000, tzinfo=timezone.utc)
    assert row.checked_in_at == datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
    assert row.checked_out_at == datetime(2026, 8, 12, 9, 27, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("status_id", [3, 5, 6, 8])
def test_upsert_non_attending_gotracker_status_cancels_appointment(status_id):
    session = _session(existing=None)
    _upsert(session, gotracker_status_id=status_id)
    assert session.add.call_args.args[0].status == "cancelled"


def test_upsert_unchanged_when_same_start_time():
    existing = SimpleNamespace(
        start_time=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
    )
    res = _upsert(_session(existing=existing))
    assert res.change == "unchanged"


def test_upsert_partial_status_update_preserves_existing_scheduling_fields():
    original_start = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    existing = SimpleNamespace(
        location_id="loc-1",
        nexhealth_patient_id="gt-595",
        contact_id="contact-1",
        provider_id="gt-3",
        appointment_type_id="gt-8",
        start_time=original_start,
        status="scheduled",
        gotracker_status_id=1,
        gotracker_status_label="booked",
        is_confirmed=False,
        is_preconfirmed=False,
        last_status_source="webhook",
        last_status_synced_at=None,
        last_event="appointment.created",
        last_synced_at=None,
        updated_at=None,
    )

    res = _upsert(
        _session(existing=existing),
        nexhealth_patient_id=None,
        contact_id=None,
        start_time=None,
        provider_id=None,
        appointment_type_id=None,
        gotracker_status_id=2,
        is_confirmed=True,
        is_preconfirmed=False,
        status_source="webhook",
    )

    assert res.change == "unchanged"
    assert existing.start_time == original_start
    assert existing.nexhealth_patient_id == "gt-595"
    assert existing.contact_id == "contact-1"
    assert existing.provider_id == "gt-3"
    assert existing.appointment_type_id == "gt-8"
    assert existing.gotracker_status_id == 2
    assert existing.gotracker_status_label == "booked_waiting"
    assert existing.is_confirmed is True
    assert existing.is_preconfirmed is False


def test_upsert_rescheduled_when_start_time_changes():
    existing = SimpleNamespace(
        start_time=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),  # different
        status="scheduled",
    )
    res = _upsert(_session(existing=existing))
    assert res.change == "rescheduled"


def test_upsert_cancelled():
    existing = SimpleNamespace(
        start_time=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
    )
    res = _upsert(_session(existing=existing), cancelled=True)
    assert res.change == "cancelled"


def test_record_gotracker_writeback_updates_existing_snapshot():
    existing = SimpleNamespace(
        location_id="loc-1",
        provider_id=None,
        start_time=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
        gotracker_status_id=1,
        gotracker_status_label="booked",
        is_confirmed=False,
        is_preconfirmed=False,
        last_status_source="webhook",
        last_status_synced_at=None,
        last_writeback_at=None,
        updated_at=None,
    )
    session = _session(existing=existing)
    row = asyncio.run(
        NexHealthProjectionService(session).record_gotracker_writeback(
            institution_id="inst-1",
            appointment_id="a-1",
            location_id="loc-1",
            status_id=3,
            confirmed=True,
            preconfirmed=False,
        )
    )
    assert row is existing
    assert existing.gotracker_status_id == 3
    assert existing.gotracker_status_label == "cancelled"
    assert existing.is_confirmed is True
    assert existing.is_preconfirmed is False
    assert existing.status == "cancelled"
    assert existing.last_status_source == "workflow_writeback"
    assert existing.last_status_synced_at is not None
    assert existing.last_writeback_at is not None


# ── claim_event idempotency ──────────────────────────────────────────────────


def test_claim_event_new_returns_true():
    session = _session(existing=None)
    svc = NexHealthProjectionService(session)
    claimed = asyncio.run(
        svc.claim_event(
            institution_id="i",
            appointment_id="a",
            event_type="appointment.updated",
            dedup_key="k",
        )
    )
    assert claimed is True
    session.add.assert_called_once()


def test_claim_event_completed_returns_false():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.COMPLETED.value,
        attempts=1,
        updated_at=datetime.now(timezone.utc),
    )
    claimed = asyncio.run(
        NexHealthProjectionService(_session(existing=existing)).claim_event(
            institution_id="i", appointment_id="a", event_type="e", dedup_key="k"
        )
    )
    assert claimed is False


def test_claim_event_failed_is_reclaimable():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.FAILED.value,
        attempts=1,
        updated_at=datetime.now(timezone.utc),
    )
    claimed = asyncio.run(
        NexHealthProjectionService(_session(existing=existing)).claim_event(
            institution_id="i", appointment_id="a", event_type="e", dedup_key="k"
        )
    )
    assert claimed is True
    assert existing.status == NexHealthWebhookStatus.PROCESSING.value


def test_claim_event_stale_processing_is_reclaimable():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.PROCESSING.value,
        attempts=1,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=10),  # > 5min TTL
    )
    claimed = asyncio.run(
        NexHealthProjectionService(_session(existing=existing)).claim_event(
            institution_id="i", appointment_id="a", event_type="e", dedup_key="k"
        )
    )
    assert claimed is True


def test_claim_event_fresh_processing_blocks():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.PROCESSING.value,
        attempts=1,
        updated_at=datetime.now(timezone.utc),  # fresh
    )
    claimed = asyncio.run(
        NexHealthProjectionService(_session(existing=existing)).claim_event(
            institution_id="i", appointment_id="a", event_type="e", dedup_key="k"
        )
    )
    assert claimed is False


def test_claim_event_stores_encrypted_raw_and_redacted_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    session = _session(existing=None)
    svc = NexHealthProjectionService(session)
    payload = {
        "id": "evt-1",
        "event_name": "patient_updated",
        "data": {"patient": {"id": "pat-1", "email": "sam@example.com"}},
    }
    raw_payload = '{"id":"evt-1","data":{"patient":{"email":"sam@example.com"}}}'

    claimed = asyncio.run(
        svc.claim_event(
            institution_id="i",
            patient_id="pat-1",
            event_type="patient_updated",
            dedup_key="k",
            source_event_id="evt-1",
            payload=payload,
            raw_payload=raw_payload,
        )
    )

    assert claimed is True
    row = session.add.call_args.args[0]
    assert row.source_event_id == "evt-1"
    assert row.payload_hash
    assert row.raw_payload == raw_payload
    assert row.raw_payload_encrypted != raw_payload
    assert row.redacted_payload["data"] == "[redacted]"
    assert row.raw_payload_retain_until is not None


# ── time-aware idempotency key (D-1) ─────────────────────────────────────────


def test_idempotency_key_changes_on_reschedule():
    k_old = make_appointment_idempotency_key("v1", "a1", "2026-08-01T10:00:00Z")
    k_new = make_appointment_idempotency_key("v1", "a1", "2026-08-02T14:00:00Z")
    assert k_old != k_new


def test_idempotency_key_same_instant_normalizes():
    assert make_appointment_idempotency_key(
        "v1", "a1", "2026-08-01T10:00:00Z"
    ) == make_appointment_idempotency_key("v1", "a1", "2026-08-01T10:00:00+00:00")


def test_idempotency_key_falls_back_without_time():
    assert make_appointment_idempotency_key("v1", "a1") == "appt:v1:a1"
