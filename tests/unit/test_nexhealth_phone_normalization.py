"""NexHealth silently truncates phone_number to first 10 chars on create
but does NOT truncate on lookup. Pre-truncating consistently in our adapter
keeps create and lookup pointing at the same key.

Verified empirically (curl):
    Create body phone="031234536985" (12 chars) → stored "0312345369" (10 chars)
    Lookup with "031234536985" → 0 hits
    Lookup with "0312345369"   → 1 hit
"""

from __future__ import annotations

import pytest

from src.app.pms.nexhealth.adapter import _normalize_phone_for_nexhealth


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Pakistan local 11-char with leading 0 — what Retell collects.
        ("03485619645", "0348561964"),
        # Same number with country-code prefix; normaliser sees first 10 digits.
        ("923485619645", "9234856196"),
        # User-typed format with separators — digits-only first.
        ("(0342) 711-2331", "0342711233"),
        ("0342 711 2331", "0342711233"),
        # Already 10 chars — passthrough.
        ("0348561964", "0348561964"),
        # US-style 10 digits — passthrough.
        ("5551234567", "5551234567"),
        # E.164 with plus and country code — first 10 digits include the country.
        ("+15551234567", "1555123456"),
    ],
)
def test_normalizes_to_first_ten_digits(raw: str, expected: str) -> None:
    assert _normalize_phone_for_nexhealth(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "()-"])
def test_returns_none_when_no_digits(raw: str | None) -> None:
    assert _normalize_phone_for_nexhealth(raw) is None
