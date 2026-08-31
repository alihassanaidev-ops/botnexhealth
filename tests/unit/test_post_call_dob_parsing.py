"""DOB normalization for Retell's post-call payloads.

The no-PMS receptionist's extraction prompt asks for ``month-DD-YYYY`` and
gives ``February-21-2001`` as its example, so the dashed shape is what that
agent actually emits. It used to fall through every format and get dropped,
which silently cost us the patient's date of birth on every call.
"""

from __future__ import annotations

import pytest

from src.app.services.post_call_service import _parse_dob


class TestAgentEmittedFormats:
    @pytest.mark.parametrize(
        "raw",
        [
            "February-21-2001",   # the agent's documented example
            "february-21-2001",   # lowercased month
            "FEBRUARY-21-2001",   # shouty
            "Feb-21-2001",        # abbreviated
        ],
    )
    def test_dashed_month_name(self, raw):
        assert _parse_dob(raw) == "2001-02-21"

    def test_real_export_value(self):
        assert _parse_dob("August-29-1961") == "1961-08-29"

    def test_single_digit_day(self):
        assert _parse_dob("March-08-1957") == "1957-03-08"


class TestOtherAcceptedFormats:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2001-02-21", "2001-02-21"),
            ("February 21, 2001", "2001-02-21"),
            ("Feb 21, 2001", "2001-02-21"),
            ("February 21 2001", "2001-02-21"),
            ("02/21/2001", "2001-02-21"),
            ("  February-21-2001  ", "2001-02-21"),
        ],
    )
    def test_parses(self, raw, expected):
        assert _parse_dob(raw) == expected


class TestRejected:
    @pytest.mark.parametrize("raw", [None, "", "   ", "None", "n/a", "N/A", "garbage", "21-02-2001"])
    def test_returns_none(self, raw):
        assert _parse_dob(raw) is None

    def test_ambiguous_day_month_is_not_silently_swapped(self):
        """"21-02-2001" has no month name and isn't ISO — better dropped than
        guessed, since guessing wrong writes a wrong DOB to the chart."""
        assert _parse_dob("21-02-2001") is None
