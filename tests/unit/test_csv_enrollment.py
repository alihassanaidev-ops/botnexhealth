"""Parsing and row resolution for CSV campaign enrollment.

A CSV is the one enrollment route where a mistake reaches hundreds of people at
once, so the parser's job is to be explicit about what it could not use rather
than quietly dropping it.
"""

from __future__ import annotations

from src.app.services.automation.csv_enrollment_service import (
    MAX_ROWS,
    csv_idempotency_key,
    normalise_phone,
    parse_csv,
)


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_a_plain_file_parses() -> None:
    preview = parse_csv(
        _csv(
            "first_name,last_name,phone\n"
            "Jordan,Rivera,+14155550100\n"
            "Sam,Okafor,+14155550101\n"
        )
    )

    assert preview.parse_errors == []
    assert len(preview.eligible) == 2
    assert preview.rows[0].first_name == "Jordan"
    assert preview.rows[0].phone == "+14155550100"
    # Line numbers count the header, so a reported row matches what the clinic
    # sees in their spreadsheet.
    assert preview.rows[0].line == 2


def test_header_spellings_a_clinic_export_actually_uses() -> None:
    preview = parse_csv(
        _csv("First Name,Cell Phone,Email Address\nJordan,4155550100,J@Example.COM\n")
    )

    row = preview.rows[0]
    assert row.first_name == "Jordan"
    assert row.phone == "+14155550100"
    # Lower-cased, because the hash it is matched on is case-sensitive.
    assert row.email == "j@example.com"


def test_a_file_with_no_usable_column_is_refused_with_a_reason() -> None:
    preview = parse_csv(_csv("name,notes\nJordan,called them\n"))

    assert preview.parse_errors
    assert "phone" in preview.parse_errors[0]
    assert preview.rows == []


def test_an_unparseable_number_is_excluded_not_stored() -> None:
    """A number we cannot read would hash to something no consent record matches."""
    preview = parse_csv(_csv("phone\nnot-a-number\n"))

    row = preview.rows[0]
    assert row.phone is None
    assert row.excluded_reason == "Phone number is not valid"


def test_a_row_with_neither_phone_nor_email_is_excluded() -> None:
    preview = parse_csv(_csv("phone,email,first_name\n,,Jordan\n"))

    assert preview.rows[0].excluded_reason == "No phone number or email address"


def test_email_only_rows_are_eligible() -> None:
    preview = parse_csv(_csv("email\njordan@example.com\n"))

    assert preview.rows[0].excluded_reason is None
    assert preview.rows[0].email == "jordan@example.com"


def test_the_row_cap_truncates_visibly() -> None:
    """Silent truncation is how an import looks successful with half the list missing."""
    rows = "\n".join(f"+1415555{i:04d}" for i in range(MAX_ROWS + 25))
    preview = parse_csv(_csv(f"phone\n{rows}\n"))

    assert len(preview.rows) == MAX_ROWS
    assert preview.truncated is True


def test_an_empty_file_says_so() -> None:
    assert parse_csv(_csv("phone\n")).parse_errors == ["No data rows found."]


def test_a_spreadsheet_byte_order_mark_does_not_break_the_header() -> None:
    """Excel writes one; without handling it the first column never matches."""
    preview = parse_csv("﻿phone\n+14155550100\n".encode("utf-8"))

    assert preview.parse_errors == []
    assert preview.rows[0].phone == "+14155550100"


def test_normalise_phone_accepts_local_formatting() -> None:
    assert normalise_phone("(415) 555-0100") == "+14155550100"
    assert normalise_phone("415-555-0100") == "+14155550100"
    assert normalise_phone("") is None
    assert normalise_phone("12") is None


def test_the_idempotency_key_is_stable_per_upload_and_contact() -> None:
    """A retried upload must not enroll the same person twice."""
    first = csv_idempotency_key("ver-1", "upload-a", "c-1")
    assert first == csv_idempotency_key("ver-1", "upload-a", "c-1")
    assert first != csv_idempotency_key("ver-1", "upload-b", "c-1")
    assert first != csv_idempotency_key("ver-1", "upload-a", "c-2")
