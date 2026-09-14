"""Reading an appointment's start out of a GoTracker payload.

Tracker payloads carry two kinds of time: unambiguous instants, and the
practice's own wall clock. Only the first means the same thing to everyone, so
it wins where present. The wall clock is where this used to go wrong — the two
fields were glued together and given a "Z", which declared the clinic's clock to
be UTC and moved every appointment built that way by the clinic's offset.
"""

from __future__ import annotations

from src.app.api.routes.gotracker_webhooks import _appointment_start_time

TORONTO = "America/Toronto"


def test_wall_clock_is_read_in_the_clinics_zone() -> None:
    """10:00 in Toronto is 14:00 UTC in July, not 10:00 UTC."""
    assert (
        _appointment_start_time(
            {"AppointmentDate": "2026-07-28", "AppointmentTime": "10:00:00"},
            timezone_name=TORONTO,
        )
        == "2026-07-28T14:00:00+00:00"
    )


def test_the_date_container_may_carry_its_own_midnight_stamp() -> None:
    """Tracker sends the date as a full timestamp; only its date half counts."""
    assert (
        _appointment_start_time(
            {
                "AppointmentDate": "2026-07-28T00:00:00.000Z",
                "AppointmentTime": "10:00:00",
            },
            timezone_name=TORONTO,
        )
        == "2026-07-28T14:00:00+00:00"
    )


def test_standard_time_uses_the_winter_offset() -> None:
    """Toronto is UTC-5 in January and UTC-4 in July. A fixed offset would be
    wrong for half the year, which is why the zone is resolved per date."""
    assert (
        _appointment_start_time(
            {"AppointmentDate": "2026-01-14", "AppointmentTime": "10:00:00"},
            timezone_name=TORONTO,
        )
        == "2026-01-14T15:00:00+00:00"
    )


def test_an_instant_field_wins_over_the_wall_clock() -> None:
    """Both describe the same appointment, and only the instant is unambiguous."""
    assert (
        _appointment_start_time(
            {
                "AppointmentDate": "2026-09-02",
                "AppointmentTime": "15:20:00",
                "start_time": "2026-09-02T19:20:00.000Z",
            },
            timezone_name=TORONTO,
        )
        == "2026-09-02T19:20:00.000Z"
    )


def test_an_instant_is_returned_verbatim() -> None:
    """Rewriting an already-unambiguous value would change the appointment's
    projection and its dedup key for no gain."""
    assert (
        _appointment_start_time(
            {"start_time": "2026-09-02T15:20:00-04:00"}, timezone_name=TORONTO
        )
        == "2026-09-02T15:20:00-04:00"
    )


def test_the_payloads_own_zone_beats_the_stored_one() -> None:
    """The Synchronizer produced these wall-clock values, so the Synchronizer
    says what they mean. 15:20 in Vancouver is 22:20 UTC, not 19:20."""
    assert (
        _appointment_start_time(
            {
                "AppointmentDate": "2026-09-02",
                "AppointmentTime": "15:20:00",
                "timezone": "America/Vancouver",
            },
            timezone_name=TORONTO,
        )
        == "2026-09-02T22:20:00+00:00"
    )


def test_a_bare_time_is_not_mistaken_for_a_start_time() -> None:
    """``appointment_time`` alone is a time of day with no date. Returning it
    produced a "start time" that named no day at all; it belongs to the
    wall-clock pair, which needs the date to mean anything."""
    assert (
        _appointment_start_time(
            {"appointment_time": "15:20:00"}, timezone_name=TORONTO
        )
        is None
    )
    assert (
        _appointment_start_time(
            {"appointment_date": "2026-09-02", "appointment_time": "15:20:00"},
            timezone_name=TORONTO,
        )
        == "2026-09-02T19:20:00+00:00"
    )


def test_an_unknown_zone_falls_back_to_utc_rather_than_rejecting() -> None:
    """An appointment read an hour out is recoverable; a webhook rejected over
    a mistyped zone loses the event."""
    assert (
        _appointment_start_time(
            {"AppointmentDate": "2026-07-28", "AppointmentTime": "10:00:00"},
            timezone_name="Mars/Olympus",
        )
        == "2026-07-28T10:00:00+00:00"
    )


def test_a_payload_with_no_usable_time_yields_nothing() -> None:
    assert _appointment_start_time({}, timezone_name=TORONTO) is None
    assert (
        _appointment_start_time(
            {"AppointmentDate": "2026-07-28"}, timezone_name=TORONTO
        )
        is None
    )


def test_an_unparseable_value_does_not_raise() -> None:
    assert (
        _appointment_start_time(
            {"AppointmentDate": "not-a-date", "AppointmentTime": "10:00:00"},
            timezone_name=TORONTO,
        )
        is None
    )
