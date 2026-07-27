"""Tests for campaign run timeline detail projection."""

from __future__ import annotations

from src.app.services.automation.campaign_operations_service import (
    _branch_from_result_code,
    _timeline_safe_mapping,
)


def test_timeline_safe_mapping_keeps_operational_context_and_redacts_phi() -> None:
    projected = _timeline_safe_mapping(
        {
            "appointment_time": "10:00 AM",
            "appointment_id": "gt-900000004",
            "call_outcome": "confirmed",
            "patient_first_name": "Jordan",
            "user_number": "+15555550123",
            "raw_payload": {"patient_name": "Jordan Rivera"},
        }
    )

    assert projected["appointment_time"] == "10:00 AM"
    assert projected["appointment_id"] == "gt-900000004"
    assert projected["call_outcome"] == "confirmed"
    assert projected["patient_first_name"] == "[redacted]"
    assert projected["user_number"] == "[redacted]"
    assert projected["raw_payload"] == "[redacted]"


def test_branch_from_result_code_returns_condition_branch() -> None:
    assert _branch_from_result_code("branch_true") == "true"
    assert _branch_from_result_code("branch_false") == "false"
    assert _branch_from_result_code("confirmed") is None
