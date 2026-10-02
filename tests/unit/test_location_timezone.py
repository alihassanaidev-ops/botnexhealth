"""Learning a clinic's timezone from what its practice software already sends.

`InstitutionLocation.timezone` defaults to "UTC" and nothing populated it, so a
clinic in Ontario silently ran on UTC. Quiet hours read the field correctly,
judged 2:40pm local as 6:40pm, and held an outbound call until a window it
thought was open — with no error anywhere. GoTracker was sending the right
answer on every appointment webhook the whole time.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.services.location_timezone import (
    UNSET_TIMEZONE,
    extract_timezone,
    is_timezone_unset,
    learn_location_timezone,
)


def _location(tz: str | None = UNSET_TIMEZONE) -> SimpleNamespace:
    return SimpleNamespace(id="loc-1", timezone=tz)


def test_an_unset_location_adopts_the_reported_zone() -> None:
    location = _location()
    assert learn_location_timezone(location, {"timezone": "America/Toronto"}) == (
        "America/Toronto"
    )
    assert location.timezone == "America/Toronto"


def test_a_deliberate_setting_is_never_overwritten() -> None:
    """We cannot tell a deliberate UTC from an unset one, so only the default
    is replaced — clobbering a real choice from a payload would be worse."""
    location = _location("America/Vancouver")
    assert learn_location_timezone(location, {"timezone": "America/Toronto"}) is None
    assert location.timezone == "America/Vancouver"


def test_an_unrecognised_zone_is_ignored_rather_than_stored() -> None:
    """A bad value would be stored and then silently fall back to UTC on every
    read, which is the original bug with extra steps."""
    location = _location()
    assert learn_location_timezone(location, {"timezone": "Mars/Olympus"}) is None
    assert location.timezone == UNSET_TIMEZONE


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "America/Halifax"},
        {"time_zone": "America/Halifax"},
        {"TimeZone": "America/Halifax"},
        {"Timezone": "America/Halifax"},
        {"location_timezone": "America/Halifax"},
    ],
)
def test_the_spellings_both_systems_use_are_accepted(payload: dict) -> None:
    assert extract_timezone(payload) == "America/Halifax"


@pytest.mark.parametrize(
    "payload", [{}, {"timezone": ""}, {"timezone": "   "}, {"timezone": 5}, None, "x"]
)
def test_a_payload_without_a_usable_zone_yields_nothing(payload: object) -> None:
    assert extract_timezone(payload) is None


def test_the_gotracker_webhook_payload_carries_it() -> None:
    """Verbatim from the recorded GoTracker delivery — this is the field that
    was being parsed and dropped."""
    appointment = {
        "AppointmentId": 1343,
        "AppointmentDate": "2026-09-04T00:00:00.000Z",
        "AppointmentTime": "14:15:00",
        "timezone": "America/Toronto",
        "start_time_local": "2026-09-04T14:15:00",
    }
    assert extract_timezone(appointment) == "America/Toronto"


def test_unset_detection_treats_blank_and_default_alike() -> None:
    assert is_timezone_unset(_location(UNSET_TIMEZONE)) is True
    assert is_timezone_unset(_location("")) is True
    assert is_timezone_unset(_location(None)) is True
    assert is_timezone_unset(_location("America/Toronto")) is False
