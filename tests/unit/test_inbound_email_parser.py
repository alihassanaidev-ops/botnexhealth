"""Unit tests for inbound email parsing.

This runs on unauthenticated input from the open internet, so the tests lean on
malformed and hostile messages as much as well-formed ones.
"""

from __future__ import annotations

import pytest

from src.app.models.inbound_email_message import InboundEmailIntent
from src.app.services.email.inbound_parser import (
    classify_intent,
    parse_mime,
    strip_quoted_reply,
)


def _mime(
    *,
    body="Thanks, see you then.",
    subject="Re: Your appointment",
    from_addr="patient@example.com",
    to="r+abc@inbound.example.com",
    extra_headers="",
    content_type="text/plain; charset=utf-8",
) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <msg-1@example.com>\r\n"
        f"In-Reply-To: <orig-1@scalenexus.ai>\r\n"
        f"Date: Mon, 25 Aug 2026 10:00:00 +0000\r\n"
        f"Content-Type: {content_type}\r\n"
        f"{extra_headers}"
        f"\r\n{body}\r\n"
    ).encode()


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def test_parses_core_fields():
    parsed = parse_mime(_mime())
    assert parsed.from_address == "patient@example.com"
    assert parsed.to_addresses == ["r+abc@inbound.example.com"]
    assert parsed.subject == "Re: Your appointment"
    assert "see you then" in parsed.body_text
    assert parsed.message_id == "<msg-1@example.com>"
    assert parsed.in_reply_to == "<orig-1@scalenexus.ai>"
    assert parsed.date is not None


def test_addresses_are_lowercased():
    parsed = parse_mime(_mime(from_addr="Patient@Example.COM"))
    assert parsed.from_address == "patient@example.com"


def test_display_names_are_stripped_from_addresses():
    parsed = parse_mime(_mime(from_addr="Jane Doe <jane@example.com>"))
    assert parsed.from_address == "jane@example.com"


def test_cc_recipients_are_captured():
    parsed = parse_mime(_mime(extra_headers="Cc: other@example.com\r\n"))
    assert "other@example.com" in parsed.all_recipients


def test_html_only_message_falls_back_to_stripped_text():
    parsed = parse_mime(
        _mime(
            body="<html><body><p>Hello <b>there</b></p></body></html>",
            content_type="text/html; charset=utf-8",
        )
    )
    assert "Hello" in parsed.body_text
    assert "<b>" not in parsed.body_text


def test_scripts_are_removed_from_html():
    parsed = parse_mime(
        _mime(
            body="<html><body><script>alert(1)</script><p>Hi</p></body></html>",
            content_type="text/html; charset=utf-8",
        )
    )
    assert "alert" not in parsed.body_text


# ---------------------------------------------------------------------------
# Malformed input — must never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not an email at all",
        b"From: \r\n\r\n",
        b"\x00\x01\x02\x03",
        b"Subject: only a subject\r\n",
    ],
)
def test_malformed_messages_do_not_raise(raw):
    """A message we cannot read is still evidence and must be recorded."""
    parsed = parse_mime(raw)
    assert parsed is not None


def test_malformed_date_is_tolerated():
    raw = b"From: a@b.com\r\nDate: not-a-date\r\n\r\nhi\r\n"
    assert parse_mime(raw).date is None


# ---------------------------------------------------------------------------
# Loop prevention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Auto-Submitted: auto-replied\r\n",
        "X-Auto-Response-Suppress: All\r\n",
        "Precedence: bulk\r\n",
        "X-Autoreply: yes\r\n",
    ],
)
def test_auto_responders_are_detected(header):
    """Two autoresponders answering each other generates mail until someone
    notices."""
    assert parse_mime(_mime(extra_headers=header)).is_auto_reply is True


def test_auto_submitted_no_is_a_real_message():
    """RFC 3834 uses 'no' for genuinely human-sent mail."""
    assert parse_mime(_mime(extra_headers="Auto-Submitted: no\r\n")).is_auto_reply is False


def test_ordinary_message_is_not_an_auto_reply():
    assert parse_mime(_mime()).is_auto_reply is False


def test_null_return_path_is_treated_as_a_bounce():
    assert parse_mime(_mime(extra_headers="Return-Path: <>\r\n")).is_bounce is True


def test_delivery_status_report_is_a_bounce():
    parsed = parse_mime(
        _mime(content_type="multipart/report; report-type=delivery-status; boundary=x")
    )
    assert parsed.is_bounce is True


# ---------------------------------------------------------------------------
# Quoted reply trimming
# ---------------------------------------------------------------------------


def test_quoted_history_is_dropped():
    """Otherwise every reply stores another copy of the same PHI."""
    body = (
        "Yes that works.\n\n"
        "On Mon, 25 Aug 2026 at 09:00, Bright Smile <hello@x.io> wrote:\n"
        "> Your appointment is on Thursday\n"
        "> Reply to confirm\n"
    )
    assert strip_quoted_reply(body) == "Yes that works."


def test_original_message_marker_is_dropped():
    body = "Confirmed.\n\n----- Original Message -----\nFrom: clinic\nBlah"
    assert strip_quoted_reply(body) == "Confirmed."


def test_quote_prefixed_lines_are_dropped():
    assert strip_quoted_reply("Sure.\n> old text\n> more old") == "Sure."


def test_our_own_footer_is_dropped():
    body = (
        "Thanks!\n\n—\nYou're receiving this because you're a patient of "
        "Bright Smile. To stop receiving emails, unsubscribe here: https://x"
    )
    assert strip_quoted_reply(body) == "Thanks!"


def test_empty_body_is_safe():
    assert strip_quoted_reply(None) == ""
    assert strip_quoted_reply("") == ""


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["STOP", "please remove me", "unsubscribe me"])
def test_opt_out_is_detected_anywhere_in_the_body(text):
    """Missing an opt-out because the word sat mid-sentence is a compliance
    failure, so this one is matched on the whole body."""
    assert classify_intent(text) == InboundEmailIntent.STOP.value


@pytest.mark.parametrize("text", ["yes", "Confirmed", "ok", "sounds good"])
def test_short_affirmatives_are_confirmations(text):
    assert classify_intent(text) == InboundEmailIntent.CONFIRM.value


def test_yes_inside_a_long_message_is_not_a_confirmation():
    """A paragraph describing a problem that happens to contain "yes" must reach
    a human, not be auto-confirmed."""
    text = (
        "Yes I got your message but my tooth is still hurting a lot and the "
        "swelling has not gone down since the procedure last week."
    )
    assert classify_intent(text) != InboundEmailIntent.CONFIRM.value


def test_reschedule_is_detected():
    assert classify_intent("Can we move it to Friday?") == (
        InboundEmailIntent.RESCHEDULE.value
    )


def test_question_is_detected():
    assert classify_intent("What time should I arrive?") == (
        InboundEmailIntent.QUESTION.value
    )


def test_auto_reply_wins_over_content():
    assert classify_intent("yes", is_auto_reply=True) == (
        InboundEmailIntent.AUTO_REPLY.value
    )


def test_clinical_free_text_stays_free_text():
    """Anything clinical must reach a human rather than being auto-handled."""
    text = "The pain is worse today and I have a fever."
    assert classify_intent(text) == InboundEmailIntent.FREE_TEXT.value


def test_empty_body_is_free_text():
    assert classify_intent("") == InboundEmailIntent.FREE_TEXT.value
    assert classify_intent(None) == InboundEmailIntent.FREE_TEXT.value
