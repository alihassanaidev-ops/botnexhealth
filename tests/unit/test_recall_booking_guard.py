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


def _recall_definition(allowed_types=None, allowed_providers=None, recall=True):
    if recall:
        trigger = {
            "type": "schedule",
            "cron": "0 9 * * *",
            "source": {"kind": "pms_recall", "recall_interval_months": 6},
        }
    else:
        trigger = {"type": "manual"}
    return {
        "trigger": trigger,
        "entry_node_id": "voice-1",
        "nodes": [
            {
                "type": "send_voice",
                "id": "voice-1",
                "retell_agent_id": "",
                "next_node_id": "exit-1",
                "allowed_appointment_type_ids": allowed_types or [],
                "allowed_provider_ids": allowed_providers or [],
            },
            {"type": "exit", "id": "exit-1", "outcome": "done"},
        ],
    }


def _session(
    attempt=("run-1", "voice-1"),
    run=None,
    version=None,
    location=None,
):
    session = AsyncMock()
    result = MagicMock()
    result.first.return_value = attempt
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(side_effect=[run, version, location])
    return session


def _run(due="2999-01-01"):
    return SimpleNamespace(
        workflow_version_id="ver-1",
        location_id="loc-1",
        trigger_metadata={"recall_type_id": "2288000", "recall_due_date": due},
    )


@pytest.mark.asyncio
async def test_missing_call_id_returns_none():
    assert await resolve_recall_constraints_for_call(AsyncMock(), None) is None


@pytest.mark.asyncio
async def test_call_without_voice_attempt_returns_none():
    session = _session(attempt=None)
    assert await resolve_recall_constraints_for_call(session, "call-1") is None


@pytest.mark.asyncio
async def test_non_recall_campaign_returns_none():
    session = _session(
        run=_run(),
        version=SimpleNamespace(definition=_recall_definition(recall=False)),
    )
    assert await resolve_recall_constraints_for_call(session, "call-1") is None


@pytest.mark.asyncio
async def test_resolves_node_constraints_and_earliest_date():
    session = _session(
        run=_run(),
        version=SimpleNamespace(
            definition=_recall_definition(
                allowed_types=["nh-1230522"],
                allowed_providers=["nh-502294485", "nh-506291620"],
            )
        ),
        location=SimpleNamespace(timezone="America/Toronto"),
    )

    constraints = await resolve_recall_constraints_for_call(session, "call-1")

    assert constraints is not None
    assert constraints.recall_type_id == "2288000"
    assert constraints.appointment_type_ids == frozenset({"1230522"})
    assert constraints.provider_ids == frozenset({"502294485", "506291620"})
    assert constraints.earliest_date == date(2999, 1, 1)


@pytest.mark.asyncio
async def test_recall_without_allowed_types_fails_closed():
    session = _session(
        run=_run(),
        version=SimpleNamespace(definition=_recall_definition(allowed_types=[])),
        location=SimpleNamespace(timezone="UTC"),
    )

    constraints = await resolve_recall_constraints_for_call(session, "call-1")

    assert constraints is not None
    assert constraints.permits_appointment_type("nh-1230522") is False


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
