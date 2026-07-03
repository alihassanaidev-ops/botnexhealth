"""Unit tests for the Plan 09 dispatch-time PMS live-revalidation service."""

from __future__ import annotations

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


def _make_session(*, location=True, institution=True):
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
