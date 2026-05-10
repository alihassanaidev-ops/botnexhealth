"""HIPAA: raw DOB must never reach log output.

DOB is a HIPAA §164.514(b)(2)(i)(C) identifier. The DOB-parser warning
path used to log ``%r`` of the raw string, leaking the actual date. This
test pins the redacted form so a future regression fails loudly.
"""

from __future__ import annotations

import logging
import re

import pytest

from src.app.services.post_call_service import _parse_dob


def test_parse_dob_does_not_log_raw_value(caplog: pytest.LogCaptureFixture) -> None:
    raw = "Maybe February 30, 1987"  # not parseable -> hits warning branch
    caplog.set_level(logging.WARNING, logger="src.app.services.post_call_service")

    result = _parse_dob(raw)

    assert result is None
    combined = " ".join(rec.getMessage() for rec in caplog.records)
    assert raw not in combined, f"Raw DOB leaked into log output: {combined!r}"
    assert "1987" not in combined, "Year fragment leaked into log output"
    assert "February" not in combined, "Month name leaked into log output"
    # Hash + length must be present so operators can still correlate.
    assert "dob_hash=" in combined
    assert re.search(r"len=\d+", combined)


def test_parse_dob_iso_passthrough_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="src.app.services.post_call_service")
    assert _parse_dob("2001-02-02") == "2001-02-02"
    assert not caplog.records, "Successful parse should not log a warning"


def test_parse_dob_human_readable_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="src.app.services.post_call_service")
    assert _parse_dob("February 2, 2001") == "2001-02-02"
    assert not caplog.records, "Successful parse should not log a warning"


def test_parse_dob_empty_inputs_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="src.app.services.post_call_service")
    for value in (None, "", "  ", "None", "n/a"):
        assert _parse_dob(value) is None
    assert not caplog.records, "Sentinel inputs should not warn"
