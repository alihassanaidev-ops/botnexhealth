"""Unit tests for picking the booked appointment type from invocation results.

The DB/name-resolution path is covered by integration tests; here we pin the
pure selection logic that must be null-safe and prefer the most recent success.
"""

import json

from src.app.services.post_call_service import _booked_appt_type_id_from_results


def test_picks_first_successful_booking():
    # Query orders most-recent-first, so the first success wins.
    rows = [
        json.dumps({"success": True, "appointment_type_id": "nh-123"}),
        json.dumps({"success": True, "appointment_type_id": "nh-999"}),
    ]
    assert _booked_appt_type_id_from_results(rows) == "nh-123"


def test_skips_null_malformed_and_unsuccessful_rows():
    rows = [
        None,
        json.dumps({"success": False, "appointment_type_id": "nh-123"}),
        "not-json{",
        json.dumps({"success": True, "appointment_type_id": "nh-456"}),
    ]
    assert _booked_appt_type_id_from_results(rows) == "nh-456"


def test_none_when_no_successful_booking_with_type():
    assert _booked_appt_type_id_from_results([]) is None
    assert _booked_appt_type_id_from_results([None, "x", json.dumps({"success": True})]) is None
    # numeric ids coerced to str
    assert _booked_appt_type_id_from_results([json.dumps({"success": True, "appointment_type_id": 42})]) == "42"
