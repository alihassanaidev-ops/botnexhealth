"""Unit tests for the NexHealth projection + event-ledger service (Plan 09)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def test_upsert_unchanged_when_same_start_time():
    existing = SimpleNamespace(
        start_time=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
    )
    res = _upsert(_session(existing=existing))
    assert res.change == "unchanged"


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


# ── claim_event idempotency ──────────────────────────────────────────────────


def test_claim_event_new_returns_true():
    session = _session(existing=None)
    svc = NexHealthProjectionService(session)
    claimed = asyncio.run(
        svc.claim_event(institution_id="i", appointment_id="a", event_type="appointment.updated", dedup_key="k")
    )
    assert claimed is True
    session.add.assert_called_once()


def test_claim_event_completed_returns_false():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.COMPLETED.value, attempts=1, updated_at=datetime.now(timezone.utc)
    )
    claimed = asyncio.run(
        NexHealthProjectionService(_session(existing=existing)).claim_event(
            institution_id="i", appointment_id="a", event_type="e", dedup_key="k"
        )
    )
    assert claimed is False


def test_claim_event_failed_is_reclaimable():
    existing = SimpleNamespace(
        status=NexHealthWebhookStatus.FAILED.value, attempts=1, updated_at=datetime.now(timezone.utc)
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


# ── time-aware idempotency key (D-1) ─────────────────────────────────────────


def test_idempotency_key_changes_on_reschedule():
    k_old = make_appointment_idempotency_key("v1", "a1", "2026-08-01T10:00:00Z")
    k_new = make_appointment_idempotency_key("v1", "a1", "2026-08-02T14:00:00Z")
    assert k_old != k_new


def test_idempotency_key_same_instant_normalizes():
    assert make_appointment_idempotency_key("v1", "a1", "2026-08-01T10:00:00Z") == \
        make_appointment_idempotency_key("v1", "a1", "2026-08-01T10:00:00+00:00")


def test_idempotency_key_falls_back_without_time():
    assert make_appointment_idempotency_key("v1", "a1") == "appt:v1:a1"
