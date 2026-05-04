"""Call.transcript and Call.summary must round-trip through encryption."""

from __future__ import annotations

import pytest

from src.app.config import settings
from src.app.models.call import Call


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "encryption_key", "A" * 43)


def test_summary_round_trips_through_encryption():
    call = Call(institution_id="i1")
    plaintext = "Patient wants to book a cleaning next Tuesday."

    call.summary = plaintext

    # Storage column is ciphertext, not plaintext.
    assert call.summary_encrypted is not None
    assert call.summary_encrypted != plaintext
    assert plaintext not in call.summary_encrypted

    # Property decrypts on read.
    assert call.summary == plaintext


def test_transcript_round_trips_through_encryption():
    call = Call(institution_id="i1")
    transcript = [
        {"role": "user", "content": "Hi, can I book?"},
        {"role": "assistant", "content": "Sure, what day?"},
    ]

    call.transcript_with_tool_calls = transcript

    assert call.transcript_with_tool_calls_encrypted is not None
    assert "book" not in call.transcript_with_tool_calls_encrypted

    decrypted = call.transcript_with_tool_calls
    assert decrypted == transcript


def test_setting_none_clears_encrypted_columns():
    call = Call(institution_id="i1")
    call.transcript_with_tool_calls = [{"role": "user", "content": "x"}]
    assert call.transcript_with_tool_calls_encrypted is not None

    call.transcript_with_tool_calls = None
    assert call.transcript_with_tool_calls_encrypted is None
    assert call.transcript_with_tool_calls is None


def test_summary_setter_handles_none():
    call = Call(institution_id="i1")
    call.summary = None
    assert call.summary_encrypted is None
    assert call.summary is None
