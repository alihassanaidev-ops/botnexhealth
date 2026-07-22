"""Display-time masking of Retell's PII-scrubbed placeholder tokens."""

from __future__ import annotations

import pytest

from src.app.services.pii_masking import MASK, mask_brackets, mask_transcript


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hi [person 1], your appointment is set.", f"Hi {MASK}, your appointment is set."),
        ("[person 1] and [person 2] both called.", f"{MASK} and {MASK} both called."),
        ("Email [email] phone [phone number].", f"Email {MASK} phone {MASK}."),
        ("No tokens at all here.", "No tokens at all here."),
        ("[DATE_OF_BIRTH]", MASK),
    ],
)
def test_mask_brackets(raw: str, expected: str) -> None:
    assert mask_brackets(raw) == expected


@pytest.mark.parametrize("empty", [None, ""])
def test_mask_brackets_passes_empty_through(empty) -> None:
    assert mask_brackets(empty) == empty


def test_mask_brackets_leaves_unclosed_bracket() -> None:
    # A lone '[' with no closing bracket is not a token — leave it untouched.
    assert mask_brackets("weird [ text") == "weird [ text"


def test_mask_transcript_masks_content_only() -> None:
    turns = [
        {"role": "agent", "content": "Hello [person 1]"},
        {"role": "tool_call_invocation", "name": "book_appointment"},
        {"role": "tool_call_result", "content": None},
        {"role": "user", "content": "My name is [person 1] and I need a cleaning"},
    ]
    out = mask_transcript(turns)
    assert out[0]["content"] == f"Hello {MASK}"
    # Non-content fields untouched.
    assert out[1] == {"role": "tool_call_invocation", "name": "book_appointment"}
    assert out[2]["content"] is None
    assert out[3]["content"] == f"My name is {MASK} and I need a cleaning"


def test_mask_transcript_does_not_mutate_input() -> None:
    turns = [{"role": "agent", "content": "Hi [person 1]"}]
    mask_transcript(turns)
    assert turns[0]["content"] == "Hi [person 1]"  # original untouched


@pytest.mark.parametrize("empty", [None, []])
def test_mask_transcript_passes_empty_through(empty) -> None:
    assert mask_transcript(empty) == empty
