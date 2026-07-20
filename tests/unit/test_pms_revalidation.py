"""Unit tests for the Plan 09 dispatch-time PMS live-revalidation service."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.revalidation import (
    NoOpRevalidator,
    PmsLiveRevalidationService,
    _same_instant,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    *,
    trigger_ref_type="appointment",
    trigger_ref_id="appt-1",
    location_id="loc-1",
    institution_id="inst-1",
    appointment_at="2026-08-01T10:00:00Z",
):
    run = MagicMock()
    run.id = "run-1"
    run.trigger_ref_type = trigger_ref_type
    run.trigger_ref_id = trigger_ref_id
    run.location_id = location_id
    run.institution_id = institution_id
    run.trigger_metadata = {"appointment_at": appointment_at} if appointment_at else {}
    return run


def _make_session(*, location=True, institution=True, projection=None):
    session = AsyncMock()
    loc = MagicMock()
    loc.nexhealth_subdomain = "clinic-sub"
    loc.nexhealth_location_id = "nexloc-1"
    inst = MagicMock()

    async def _get(model, pk):
        name = getattr(model, "__name__", "")
        if name == "InstitutionLocation":
            return loc if location else None
        if name == "Institution":
            return inst if institution else None
        return None

    session.get = AsyncMock(side_effect=_get)

    # The freshness-window check queries appointment_working_set first. Default:
    # no projection row → fall through to the live NexHealth read these tests cover.
    proj_result = MagicMock()
    proj_result.scalar_one_or_none.return_value = projection
    session.execute = AsyncMock(return_value=proj_result)
    return session


def _patch_adapter(appt):
    """Patch NexHealthAdapter.create → adapter whose get_appointment returns appt."""
    adapter = AsyncMock()
    adapter.get_appointment = AsyncMock(return_value=appt)
    adapter.close = AsyncMock()
    return patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), adapter


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# NoOpRevalidator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_revalidator_never_skips():
    assert await NoOpRevalidator().revalidate(_make_run()) is None


# ---------------------------------------------------------------------------
# _same_instant
# ---------------------------------------------------------------------------


def test_same_instant_equal_across_formats():
    assert _same_instant("2026-08-01T10:00:00Z", "2026-08-01T10:00:00+00:00") is True


def test_same_instant_detects_difference():
    assert _same_instant("2026-08-01T10:00:00Z", "2026-08-01T14:00:00Z") is False


def test_same_instant_unparseable_is_failopen_true():
    assert _same_instant("garbage", "2026-08-01T10:00:00Z") is True


# ---------------------------------------------------------------------------
# PmsLiveRevalidationService.revalidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidate_skips_cancelled_appointment():
    run = _make_run()
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    appt = {"id": "appt-1", "cancelled": True, "start_time": "2026-08-01T10:00:00Z"}
    p, adapter = _patch_adapter(appt)
    with p:
        result = await svc.revalidate(run)

    assert result == "skipped_cancelled"
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_revalidate_skips_rescheduled_appointment():
    run = _make_run(appointment_at="2026-08-01T10:00:00Z")
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    appt = {"id": "appt-1", "cancelled": False, "start_time": "2026-08-01T14:00:00Z"}
    p, _ = _patch_adapter(appt)
    with p:
        result = await svc.revalidate(run)

    assert result == "skipped_rescheduled"


# ---------------------------------------------------------------------------
# Freshness window (D-2) — a fresh projection row is trusted; no live call
# ---------------------------------------------------------------------------


def _fresh_projection(*, status="scheduled", start_time="2026-08-01T10:00:00Z"):
    from datetime import datetime, timezone
    row = MagicMock()
    row.status = status
    row.last_synced_at = datetime.now(timezone.utc)
    row.start_time = (
        datetime.fromisoformat(start_time.replace("Z", "+00:00")) if start_time else None
    )
    return row


@pytest.mark.asyncio
async def test_fresh_projection_cancelled_skips_without_live_call():
    run = _make_run()
    session = _make_session(projection=_fresh_projection(status="cancelled"))
    svc = PmsLiveRevalidationService(session)

    # Patch create to blow up if called — proves no live NexHealth read happened.
    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(side_effect=AssertionError("live call must not happen when projection is fresh")),
    ):
        result = await svc.revalidate(run)
    assert result == "skipped_cancelled"


@pytest.mark.asyncio
async def test_fresh_projection_matching_time_proceeds_without_live_call():
    run = _make_run(appointment_at="2026-08-01T10:00:00Z")
    session = _make_session(projection=_fresh_projection(start_time="2026-08-01T10:00:00Z"))
    svc = PmsLiveRevalidationService(session)
    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(side_effect=AssertionError("live call must not happen when projection is fresh")),
    ):
        result = await svc.revalidate(run)
    assert result is None


@pytest.mark.asyncio
async def test_stale_projection_falls_through_to_live_read():
    from datetime import datetime, timedelta, timezone
    run = _make_run()
    stale = MagicMock()
    stale.status = "scheduled"
    stale.last_synced_at = datetime.now(timezone.utc) - timedelta(hours=1)  # stale
    stale.start_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    session = _make_session(projection=stale)
    svc = PmsLiveRevalidationService(session)

    appt = {"id": "appt-1", "cancelled": True, "start_time": "2026-08-01T10:00:00Z"}
    p, adapter = _patch_adapter(appt)
    with p:
        result = await svc.revalidate(run)
    assert result == "skipped_cancelled"  # came from the live read
    adapter.get_appointment.assert_awaited_once()


@pytest.mark.asyncio
async def test_revalidate_skips_when_projection_stale_and_pms_read_unhealthy():
    run = _make_run()
    sync_status = SimpleNamespace(
        read_status="red",
        write_status="green",
        last_checked_at=datetime.now(timezone.utc),
    )
    session = _make_session(projection=None)
    session.execute = AsyncMock(side_effect=[_result(None), _result(sync_status)])
    svc = PmsLiveRevalidationService(session)

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(side_effect=AssertionError("live call must not happen when PMS read is red")),
    ):
        result = await svc.revalidate(run)

    assert result == "skipped_pms_read_unhealthy"


@pytest.mark.asyncio
async def test_revalidate_returns_none_for_active_unchanged_appointment():
    run = _make_run(appointment_at="2026-08-01T10:00:00Z")
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    appt = {"id": "appt-1", "cancelled": False, "start_time": "2026-08-01T10:00:00Z"}
    p, _ = _patch_adapter(appt)
    with p:
        result = await svc.revalidate(run)

    assert result is None


@pytest.mark.asyncio
async def test_revalidate_failopen_on_lookup_error():
    run = _make_run()
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    # Adapter.create raises → revalidate must fail open (return None).
    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(side_effect=RuntimeError("nexhealth down")),
    ):
        result = await svc.revalidate(run)

    assert result is None


@pytest.mark.asyncio
async def test_revalidate_failopen_when_appointment_not_found():
    run = _make_run()
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    p, _ = _patch_adapter(None)  # get_appointment → None
    with p:
        result = await svc.revalidate(run)

    assert result is None


@pytest.mark.asyncio
async def test_revalidate_returns_none_for_non_appointment_run():
    run = _make_run(trigger_ref_type="recall", trigger_ref_id="pat-1")
    session = _make_session()
    svc = PmsLiveRevalidationService(session)

    # No adapter should ever be built; session.get should not be called.
    result = await svc.revalidate(run)
    assert result is None
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_revalidate_returns_none_when_no_ref_id():
    run = _make_run(trigger_ref_id=None)
    session = _make_session()
    svc = PmsLiveRevalidationService(session)
    assert await svc.revalidate(run) is None


@pytest.mark.asyncio
async def test_revalidate_failopen_when_location_not_wired_to_nexhealth():
    run = _make_run()
    session = AsyncMock()
    loc = MagicMock()
    loc.nexhealth_subdomain = None
    loc.nexhealth_location_id = None
    inst = MagicMock()

    async def _get(model, pk):
        name = getattr(model, "__name__", "")
        return loc if name == "InstitutionLocation" else inst

    session.get = AsyncMock(side_effect=_get)
    svc = PmsLiveRevalidationService(session)
    assert await svc.revalidate(run) is None
