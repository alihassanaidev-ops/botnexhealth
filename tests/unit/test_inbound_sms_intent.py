"""STOP/HELP/START detection must catch the keyword anywhere in the body."""

from __future__ import annotations

import pytest

from src.app.api.routes.twilio_webhooks import _classify_intent


@pytest.mark.parametrize(
    "body,expected",
    [
        ("STOP", "STOP"),
        ("stop", "STOP"),
        ("STOP!", "STOP"),
        ("Please STOP calling me", "STOP"),
        ("please stop", "STOP"),
        ("UNSUBSCRIBE", "STOP"),
        ("cancel my notifications", "STOP"),
        ("END", "STOP"),
        ("START", "START"),
        ("Yes, START please", "START"),
        ("HELP", "HELP"),
        ("more info please", "HELP"),
        # STOP wins over START in the unlikely "STOP and START" case.
        ("STOP and START", "STOP"),
        # No keyword token → empty.
        ("", ""),
        ("Thanks!", ""),
        ("STOPPING by tomorrow", ""),  # not a whole-word STOP
        ("CANCELLATION confirmed", ""),  # not a whole-word CANCEL
    ],
)
def test_classify_intent_finds_keywords_anywhere(body: str, expected: str) -> None:
    assert _classify_intent(body) == expected
