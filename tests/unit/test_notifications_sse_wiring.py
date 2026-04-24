"""Unit tests for SSE events fired by the call-notification task."""

from __future__ import annotations

import pytest

from src.app.models.call import CallStatus
from src.app.tasks.notifications import _sse_events_for_new_call


def test_regular_call_publishes_calls_and_dashboard() -> None:
    assert _sse_events_for_new_call(CallStatus.APPOINTMENT_BOOKED.value) == [
        "calls_updated",
        "dashboard_updated",
    ]


def test_needs_callback_also_publishes_callbacks_updated() -> None:
    events = _sse_events_for_new_call(CallStatus.NEEDS_CALLBACK.value)
    assert events == ["calls_updated", "dashboard_updated", "callbacks_updated"]


def test_missing_call_status_still_publishes_core_events() -> None:
    assert _sse_events_for_new_call(None) == ["calls_updated", "dashboard_updated"]
    assert _sse_events_for_new_call("") == ["calls_updated", "dashboard_updated"]


@pytest.mark.parametrize(
    "status",
    [
        CallStatus.NEEDS_CALLBACK.value.upper(),
        f"  {CallStatus.NEEDS_CALLBACK.value}",  # leading whitespace
    ],
)
def test_needs_callback_matching_is_normalized(status: str) -> None:
    # Upper-case variant is normalized by .lower() and included.
    events = _sse_events_for_new_call(status.strip())
    assert ("callbacks_updated" in events) is (
        status.strip().lower() == CallStatus.NEEDS_CALLBACK.value
    )
