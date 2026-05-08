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
        # ── US / Canada (NANP) — multiple shapes, all canonical 10 digits ──
        # Strict E.164 with plus and country code.
        ("+15054821234", "5054821234"),
        # Same digits, plus-less.
        ("15054821234", "5054821234"),
        # Bare 10-digit NANP.
        ("5054821234", "5054821234"),
        # User-typed with separators (US convention).
        ("(505) 482-1234", "5054821234"),
        ("505-482-1234", "5054821234"),
        ("1 (505) 482-1234", "5054821234"),
        ("1-505-482-1234", "5054821234"),
        ("+1 505 482 1234", "5054821234"),
        # Another valid NANP area code (212 NYC).
        ("+12125551234", "2125551234"),
        # ── Pakistan ──
        # Local 11-digit form with leading 0 — most common.
        ("03485619645", "0348561964"),
        # Same number with separators.
        ("(0348) 561-9645", "0348561964"),
        ("0348-561-9645", "0348561964"),
        # ── Other shapes — fall back to first-10 (matches NexHealth's
        # storage truncation so lookup still finds the row) ──
        ("923485619645", "9234856196"),
        ("0348561964", "0348561964"),  # already 10-digit non-NANP-leading
    ],
)
def test_normalizes_phone_for_nexhealth(raw: str, expected: str) -> None:
    assert _normalize_phone_for_nexhealth(raw) == expected


def test_us_canonical_form_collapses_across_input_styles() -> None:
    """All the ways an LLM/operator can write a US number must collapse
    to the same 10-digit canonical so that a create followed by any
    style of lookup will hit the same NexHealth row."""
    inputs = [
        "+15054821234",
        "15054821234",
        "5054821234",
        "(505) 482-1234",
        "505-482-1234",
        "1 (505) 482-1234",
        "+1 505-482-1234",
        "  +1 505 482 1234  ",
    ]
    canonical = {_normalize_phone_for_nexhealth(p) for p in inputs}
    assert canonical == {"5054821234"}


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "()-"])
def test_returns_none_when_no_digits(raw: str | None) -> None:
    assert _normalize_phone_for_nexhealth(raw) is None
