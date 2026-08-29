"""Parsing/mapping unit tests for the historical call-export importer.

These cover the pure half of ``import_calls_from_export`` — everything that
turns a markdown block into the fields a ``Call`` row needs. The loader half
needs a database and is exercised separately.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from src.app.scripts.import_calls_from_export import (
    _clean,
    _parse_bool,
    _parse_duration,
    _parse_export_dob,
    _parse_export_dt,
    build_custom_analysis_data,
    normalize_status,
    parse_agent_id,
    parse_markdown_export,
    transcript_to_turns,
)

# A faithful miniature of the export: header, a full call, and a
# "not connected" call whose variables table is just the placeholder row.
EXPORT = """# Phase 1 (calls 1-2 of 2) - Olive Tree Dental HyperMVP

**Agent:** Olive tree Dental - HyperMVP (`agent_ca8939cd46a6ad2c46f56c2dab`)
**Total phone calls:** 2

---

### 1. `call_aaa111`

| Field | Value |
|---|---|
| **Date / Time (EDT)** | 2026-08-28 17:26:36 |
| **Duration** | 4m 11s |
| **From** | +15196976145 |
| **Direction** | inbound |
| **Call Status** | ended |
| **Ended Reason** | `agent_hangup` |
| **User Sentiment** | Positive |

**Extracted Variables**

| Variable | Value |
|---|---|
| First Name | Hala |
| Last Name | Abu Lughod |
| Date Of Birth | August-29-1961 |
| New Patient? | No |
| Emergency | No |
| Availability | Wednesday through Friday |
| Call Status | Needs Reschedule |

**Summary:** Hala called to reschedule her Monday appointment.

**Recording URL:** https://example.test/recording.wav

**Transcript**

```
Agent: Hi, how can I help?
User: I need to reschedule.
```

---

### 2. `call_bbb222`

| Field | Value |
|---|---|
| **Date / Time (EDT)** | 2026-08-12 18:09:36 |
| **Duration** | 0s |
| **From** | +15197018627 |
| **Direction** | inbound |
| **Call Status** | not_connected |
| **Ended Reason** | `user_hangup` |
| **User Sentiment** | Unknown |

**Extracted Variables**

| Variable | Value |
|---|---|
| Call Status | No Action Needed |

**Recording URL:** - (no recording)

**Transcript**

```
(no transcript - call not connected / no audio)
```

---
"""


@pytest.fixture
def calls():
    return parse_markdown_export(EXPORT)


class TestScalarParsers:
    @pytest.mark.parametrize(
        "raw,expected",
        [("4m 11s", 251), ("32s", 32), ("0s", 0), ("7m 2s", 422), ("", None), (None, None)],
    )
    def test_duration(self, raw, expected):
        assert _parse_duration(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["-", "—", "Unknown", "Not provided", "N/A", "none", "  ", ""]
    )
    def test_placeholders_become_none(self, raw):
        assert _clean(raw) is None

    def test_clean_keeps_real_values(self):
        assert _clean("  Hala  ") == "Hala"

    @pytest.mark.parametrize(
        "raw,expected",
        [("August-29-1961", "1961-08-29"), ("March-08-1957", "1957-03-08"),
         ("June-21-2026", "2026-06-21"), ("garbage", None), ("Unknown", None)],
    )
    def test_dob(self, raw, expected):
        assert _parse_export_dob(raw) == expected

    def test_bool(self):
        assert _parse_bool("Yes") is True
        assert _parse_bool("No") is False
        assert _parse_bool(None) is False

    def test_timestamp_converts_clinic_local_to_utc(self):
        # 17:26 EDT (UTC-4) is 21:26 UTC — the column stores UTC.
        parsed = _parse_export_dt("2026-08-28 17:26:36")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.astimezone(timezone.utc).hour == 21
        assert parsed.astimezone(timezone.utc).minute == 26

    def test_timestamp_rejects_junk(self):
        assert _parse_export_dt("not a date") is None


class TestStatusNormalization:
    def test_single(self):
        assert normalize_status("Needs Reschedule") == ("needs_reschedule", "needs_reschedule")

    def test_multi_keeps_order_and_first_is_primary(self):
        primary, tags = normalize_status("Emergency, Needs Booking, Complaint")
        assert primary == "emergency"
        assert tags == "emergency,needs_booking,complaint"

    def test_agent_casing_is_accepted(self):
        """The agent's own list is mixed-case ("Needs booking", "complaint")."""
        assert normalize_status("Needs booking")[0] == "needs_booking"
        assert normalize_status("complaint")[0] == "complaint"

    def test_nopms_aliases(self):
        assert normalize_status("Needs Call Back")[0] == "needs_callback"
        assert normalize_status("Financial")[0] == "financial_inquiry"
        assert normalize_status("Insurance and Billing")[0] == "insurance_and_billing"

    def test_unknown_token_skipped_not_fatal(self):
        assert normalize_status("Not A Status, Emergency") == ("emergency", "emergency")

    def test_all_unknown_yields_none(self):
        assert normalize_status("Not A Status") == (None, None)

    def test_empty(self):
        assert normalize_status(None) == (None, None)
        assert normalize_status("") == (None, None)


class TestTranscript:
    def test_turns(self):
        turns = transcript_to_turns("Agent: Hello there\nUser: Hi back")
        assert turns == [
            {"role": "agent", "content": "Hello there"},
            {"role": "user", "content": "Hi back"},
        ]

    def test_continuation_lines_join_previous_turn(self):
        turns = transcript_to_turns("Agent: first line\nsecond line\nUser: ok")
        assert turns[0]["content"] == "first line second line"

    def test_placeholder_is_none(self):
        assert transcript_to_turns("(no transcript - call not connected / no audio)") is None

    def test_empty_is_none(self):
        assert transcript_to_turns("") is None
        assert transcript_to_turns(None) is None


class TestBlockParsing:
    def test_finds_every_call(self, calls):
        assert [c.index for c in calls] == [1, 2]
        assert [c.retell_call_id for c in calls] == ["call_aaa111", "call_bbb222"]

    def test_agent_id_from_header(self):
        assert parse_agent_id(EXPORT) == "agent_ca8939cd46a6ad2c46f56c2dab"

    def test_full_call_fields(self, calls):
        call = calls[0]
        assert call.full_name == "Hala Abu Lughod"
        assert call.date_of_birth == "1961-08-29"
        assert call.from_number == "+15196976145"
        assert call.direction == "inbound"
        assert call.sentiment == "Positive"
        assert call.disconnection_reason == "agent_hangup"  # backticks stripped
        assert call.duration_seconds == 251
        assert call.availability == "Wednesday through Friday"
        assert call.summary == "Hala called to reschedule her Monday appointment."
        assert call.recording_url == "https://example.test/recording.wav"
        assert len(transcript_to_turns(call.transcript)) == 2

    def test_post_call_status_not_confused_with_lifecycle_status(self, calls):
        """Both tables carry a "Call Status" label; only the Extracted
        Variables one is the classification we import."""
        assert calls[0].meta["Call Status"] == "ended"
        assert calls[0].call_status_raw == "Needs Reschedule"

    def test_not_connected_call_degrades_gracefully(self, calls):
        call = calls[1]
        assert call.call_status_raw == "No Action Needed"
        assert call.full_name is None
        assert call.date_of_birth is None
        assert call.sentiment is None          # "Unknown" -> NULL
        assert call.disconnection_reason == "user_hangup"
        assert call.summary is None
        assert call.recording_url is None      # "- (no recording)" -> NULL
        assert transcript_to_turns(call.transcript) is None
        assert call.duration_seconds == 0

    def test_crlf_export_parses(self):
        assert len(parse_markdown_export(EXPORT.replace("\n", "\r\n"))) == 2


class TestCustomAnalysisData:
    def test_keys_match_live_agent_schema(self, calls):
        """Keys must match the agent exactly — including the trailing space on
        "Availability " — or CustomFieldService won't resolve the definitions."""
        data = build_custom_analysis_data(calls[0])
        assert set(data) == {
            "First Name", "Last Name", "Emergency", "Availability ",
            "Date of birth", "New Patient?", "Call Status",
        }
        assert data["Availability "] == "Wednesday through Friday"
        assert data["Date of birth"] == "1961-08-29"
        assert data["New Patient?"] is False
        assert data["Emergency"] is False
        assert data["Call Status"] == "Needs Reschedule"


class TestDisconnectionReason:
    """``calls.disconnection_reason`` (varchar(80)) is populated from the
    export's ``Ended Reason`` cell, which arrives wrapped in backticks."""

    @pytest.mark.parametrize(
        "raw", ["user_hangup", "agent_hangup", "max_duration_reached"]
    )
    def test_every_value_the_export_emits(self, raw):
        assert _clean(f"`{raw}`") == raw
        assert len(raw) <= 80

    def test_missing_cell_is_none(self):
        [call] = parse_markdown_export(
            "### 1. `call_zzz`\n\n| Field | Value |\n|---|---|\n"
            "| **Direction** | inbound |\n\n**Extracted Variables**\n\n"
            "| Variable | Value |\n|---|---|\n| Call Status | No Action Needed |\n"
        )
        assert call.disconnection_reason is None


class TestAvailability:
    """Caller availability has its own column (``calls.requested_availability``);
    ``next_action`` mirrors Retell's "Appointment Detail", which the no-PMS
    agent never emits, so it stays NULL on imported rows."""

    def test_parsed_from_export(self, calls):
        assert calls[0].availability == "Wednesday through Friday"

    def test_absent_when_not_collected(self, calls):
        assert calls[1].availability is None

    def test_placeholder_cells_are_null(self):
        [call] = parse_markdown_export(
            "### 1. `call_zzz`\n\n| Field | Value |\n|---|---|\n"
            "| **Direction** | inbound |\n\n**Extracted Variables**\n\n"
            "| Variable | Value |\n|---|---|\n"
            "| Availability | Not provided |\n"
            "| Call Status | No Action Needed |\n"
        )
        assert call.availability is None
