"""Task 13 — the clinic-facing connector-health read.

The dashboard warns clinic staff when their GoTracker connection drops and
clears it on recovery, from the health the GoTracker cloud already holds.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.api.routes import connector_health as route


def _user():
    return SimpleNamespace(institution_id="inst-1", location_id="loc-1", role="STAFF")


def _pms(source="gotracker", health=None, exc=None):
    pms = SimpleNamespace(source=source)
    pms.get_connector_health = AsyncMock(
        side_effect=exc, return_value=health if health is not None else {}
    )
    pms.close = AsyncMock()
    return pms


def _call(pms):
    return asyncio.run(
        route.get_connector_health(current_user=_user(), pms=pms)
    )


def test_nexhealth_clinic_is_not_applicable():
    result = _call(_pms(source="nexhealth"))
    assert result.applicable is False
    assert result.status == "not_applicable"
    assert result.warning is False


def test_gotracker_without_health_method_is_not_applicable():
    pms = SimpleNamespace(source="gotracker", close=AsyncMock())
    result = _call(pms)
    assert result.applicable is False


def test_online_connection_does_not_warn():
    result = _call(
        _pms(health={"online": True, "connection_status": "online", "connector_version": "2.2.1"})
    )
    assert result.applicable is True
    assert result.status == "online"
    assert result.warning is False
    assert result.connector_version == "2.2.1"


@pytest.mark.parametrize("state", ["offline", "degraded", "never_connected"])
def test_unhealthy_states_warn(state):
    result = _call(_pms(health={"online": False, "connection_status": state}))
    assert result.applicable is True
    assert result.status == state
    assert result.warning is True
    assert result.message


def test_missing_status_but_online_flag_is_healthy():
    result = _call(_pms(health={"online": True}))
    assert result.status == "online"
    assert result.warning is False


def test_unknown_state_falls_back_to_warning_when_not_online():
    result = _call(_pms(health={"connection_status": "weird", "online": False}))
    assert result.status == "unknown"
    assert result.warning is True


def test_cloud_failure_warns_and_never_raises():
    pms = _pms(exc=RuntimeError("cloud down"))
    result = _call(pms)
    assert result.applicable is True
    assert result.status == "unknown"
    assert result.warning is True
    pms.close.assert_awaited()


def test_counts_are_normalized_to_ints():
    result = _call(
        _pms(
            health={
                "online": False,
                "connection_status": "offline",
                "queue_depth": "12",
                "failed_write_count": 3.0,
                "conflict_count": None,
            }
        )
    )
    assert result.queue_depth == 12
    assert result.failed_write_count == 3
    assert result.conflict_count is None
