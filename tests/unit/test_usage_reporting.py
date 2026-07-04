"""Unit tests for the usage reporting route helpers (Plan 11 M-2)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from src.app.api.routes.usage_reporting import _resolve_window

_TODAY = date(2026, 6, 15)


def test_resolve_window_defaults_to_last_30_days():
    start, end = _resolve_window(None, None, _TODAY)
    assert end == _TODAY
    assert start == _TODAY - timedelta(days=29)


def test_resolve_window_clamps_future_end_to_today():
    start, end = _resolve_window(None, _TODAY + timedelta(days=10), _TODAY)
    assert end == _TODAY


def test_resolve_window_honors_explicit_range():
    start, end = _resolve_window(date(2026, 6, 1), date(2026, 6, 10), _TODAY)
    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 10)


def test_resolve_window_rejects_inverted_range():
    with pytest.raises(HTTPException) as exc:
        _resolve_window(date(2026, 6, 10), date(2026, 6, 1), _TODAY)
    assert exc.value.status_code == 400


def test_resolve_window_rejects_overlong_range():
    with pytest.raises(HTTPException) as exc:
        _resolve_window(date(2020, 1, 1), date(2026, 6, 1), _TODAY)
    assert exc.value.status_code == 400
