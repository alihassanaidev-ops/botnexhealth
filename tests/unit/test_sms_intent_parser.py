"""Deterministic SMS patient-response parsing."""

from __future__ import annotations

import pytest

from src.app.services.automation.sms_intent_parser import parse_sms_intent


@pytest.mark.parametrize(
    "body,intent,outcome,handoff",
    [
        ("YES", "confirm", "confirmed_by_reply", None),
        ("C", "confirm", "confirmed_by_reply", None),
        ("yes but reschedule", "reschedule_requested", "staff_handoff_required", "reschedule_requested"),
        ("R", "reschedule_requested", "staff_handoff_required", "reschedule_requested"),
        ("please move my appointment", "reschedule_requested", "staff_handoff_required", "reschedule_requested"),
        ("cancel my appointment", "cancel_requested", "staff_handoff_required", "cancel_requested"),
        ("cancel my notifications", "stop", "sms_opt_out", None),
        ("CANCEL", "stop", "sms_opt_out", None),
        ("HELP", "help", "help_requested", None),
        ("I have pain after the visit", "clinical_question", "staff_handoff_required", "clinical_question"),
        ("call me please", "staff_requested", "staff_handoff_required", "patient_asks_for_staff"),
        ("this bill looks wrong", "billing_question", "staff_handoff_required", "billing_question"),
        ("what time is this", "free_text", "staff_handoff_required", "free_text"),
    ],
)
def test_parse_sms_intent(body: str, intent: str, outcome: str, handoff: str | None) -> None:
    result = parse_sms_intent(body)
    assert result.intent == intent
    assert result.outcome == outcome
    assert result.handoff_reason == handoff
