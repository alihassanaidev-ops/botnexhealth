"""Unit tests for live-call recall booking constraints."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.automation.recall_booking_guard import (
    RecallBookingConstraints,
    resolve_recall_constraints_for_call,
)


def _session(run_id="run-1", run=None, location=None):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run_id
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(side_effect=[run, location])
    return session


def _location():
    return SimpleNamespace(
        timezone="America/Toronto",
        recall_config={
            "approved_recall_type_ids": ["2288000"],
            "due_within_days": 30,
            "include_overdue": True,
            "appointment_type_map": {"2288000": "nh-1230522"},
            "provider_ids": ["nh-502294485", "nh-506291620"],
        },
    )


@pytest.mark.asyncio
async def test_missing_call_id_returns_none():
    assert await resolve_recall_constraints_for_call(AsyncMock(), None) is None


@pytest.mark.asyncio
async def test_call_without_voice_attempt_returns_none():
    session = _session(run_id=None)
    assert await resolve_recall_constraints_for_call(session, "call-1") is None


@pytest.mark.asyncio
async def test_unconfigured_location_returns_none():
    run = SimpleNamespace(location_id="loc-1", trigger_metadata={})
    location = SimpleNamespace(timezone="UTC", recall_config=None)
    session = _session(run=run, location=location)
    assert await resolve_recall_constraints_for_call(session, "call-1") is None


@pytest.mark.asyncio
async def test_resolves_mapped_type_providers_and_earliest_date():
    run = SimpleNamespace(
        location_id="loc-1",
        trigger_metadata={"recall_type_id": "2288000", "recall_due_date": "2999-01-01"},
    )
    session = _session(run=run, location=_location())

    constraints = await resolve_recall_constraints_for_call(session, "call-1")

    assert constraints is not None
    assert constraints.recall_type_id == "2288000"
    assert constraints.appointment_type_ids == frozenset({"1230522"})
    assert constraints.provider_ids == frozenset({"502294485", "506291620"})
    # Future due date becomes the earliest bookable date.
    assert constraints.earliest_date == date(2999, 1, 1)


@pytest.mark.asyncio
async def test_past_due_date_earliest_is_today():
    run = SimpleNamespace(
        location_id="loc-1",
        trigger_metadata={"recall_type_id": "2288000", "recall_due_date": "2020-01-01"},
    )
    session = _session(run=run, location=_location())

    constraints = await resolve_recall_constraints_for_call(session, "call-1")

    assert constraints is not None
    assert constraints.earliest_date is not None
    assert constraints.earliest_date >= date(2020, 1, 1)
    assert constraints.earliest_date <= date.today().replace(year=date.today().year + 1)


def test_missing_mapping_fails_closed_for_type():
    constraints = RecallBookingConstraints(
        recall_type_id="2288000",
        appointment_type_ids=frozenset(),
        provider_ids=frozenset(),
        earliest_date=None,
    )
    assert constraints.permits_appointment_type("nh-1230522") is False


def test_permits_provider_prefix_tolerance():
    constraints = RecallBookingConstraints(
        recall_type_id=None,
        appointment_type_ids=frozenset({"1230522"}),
        provider_ids=frozenset({"502294485"}),
        earliest_date=None,
    )
    assert constraints.permits_provider("nh-502294485") is True
    assert constraints.permits_provider("502294485") is True
    assert constraints.permits_provider("nh-999") is False
    assert constraints.permits_appointment_type("nh-1230522") is True
    assert constraints.permits_appointment_type("1230522") is True
