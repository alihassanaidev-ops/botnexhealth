"""Deterministic v1 parser for campaign SMS patient replies."""

from __future__ import annotations

import re
from dataclasses import dataclass


STOP_KEYWORDS = {
    "STOP",
    "STOPALL",
    "UNSUBSCRIBE",
    "END",
    "QUIT",
    "ARRET",
    "ARRÊT",
    "DESABONNER",
    "DÉSABONNER",
    "RETIRER",
    "SUPPRIMER",
}
CANCEL_KEYWORDS = {"CANCEL"}
START_KEYWORDS = {"START", "UNSTOP"}
HELP_KEYWORDS = {"HELP", "INFO", "AIDE"}
CONFIRMATION_KEYWORDS = {"YES", "Y", "CONFIRM", "C", "1"}
RESCHEDULE_KEYWORDS = {"RESCHEDULE", "RE-SCHEDULE", "REBOOK", "R"}
MOVE_KEYWORDS = {"MOVE", "CHANGE"}
APPOINTMENT_TERMS = {"APPOINTMENT", "APPT", "VISIT", "BOOKING", "SCHEDULE"}
OPT_OUT_TERMS = {"TEXTS", "TEXT", "SMS", "MESSAGES", "NOTIFICATIONS", "CALLING"}
STAFF_TERMS = {"STAFF", "PERSON", "HUMAN", "CALL", "CALLBACK", "REPRESENTATIVE"}
BILLING_TERMS = {"BILL", "BILLING", "PAYMENT", "INSURANCE", "CHARGE"}
CLINICAL_TERMS = {"PAIN", "HURT", "SWELLING", "BLEEDING", "MEDICATION", "PRESCRIPTION"}

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SmsIntentResult:
    intent: str
    outcome: str | None = None
    handoff_reason: str | None = None
    compliance_keyword: str | None = None

    @property
    def requires_handoff(self) -> bool:
        return self.handoff_reason is not None


class SmsIntentParser:
    """Keyword-only parser. No generated/NLU classification in Plan 04 v1."""

    def parse(self, body: str | None) -> SmsIntentResult:
        text = (body or "").strip()
        tokens = set(_TOKEN_RE.findall(text.upper()))
        ordered = _TOKEN_RE.findall(text.upper())

        if not tokens:
            return SmsIntentResult("free_text", handoff_reason="free_text")

        if tokens & STOP_KEYWORDS:
            return SmsIntentResult("stop", outcome="sms_opt_out", compliance_keyword="STOP")
        if tokens & START_KEYWORDS:
            return SmsIntentResult("start", outcome="sms_opt_in", compliance_keyword="START")
        if tokens & HELP_KEYWORDS:
            return SmsIntentResult("help", outcome="help_requested", compliance_keyword="HELP")

        if tokens & CANCEL_KEYWORDS:
            if tokens & APPOINTMENT_TERMS:
                return SmsIntentResult(
                    "cancel_requested",
                    outcome="staff_handoff_required",
                    handoff_reason="cancel_requested",
                )
            if tokens & OPT_OUT_TERMS or len(tokens) == 1:
                return SmsIntentResult("stop", outcome="sms_opt_out", compliance_keyword="STOP")
            return SmsIntentResult(
                "ambiguous_response",
                outcome="staff_handoff_required",
                handoff_reason="ambiguous_response",
            )

        if tokens & RESCHEDULE_KEYWORDS or (
            (tokens & MOVE_KEYWORDS) and (tokens & APPOINTMENT_TERMS)
        ):
            return SmsIntentResult(
                "reschedule_requested",
                outcome="staff_handoff_required",
                handoff_reason="reschedule_requested",
            )

        if len(ordered) == 1 and ordered[0] in CONFIRMATION_KEYWORDS:
            return SmsIntentResult("confirm", outcome="confirmed_by_reply")

        if tokens & STAFF_TERMS:
            return SmsIntentResult(
                "staff_requested",
                outcome="staff_handoff_required",
                handoff_reason="patient_asks_for_staff",
            )
        if tokens & BILLING_TERMS:
            return SmsIntentResult(
                "billing_question",
                outcome="staff_handoff_required",
                handoff_reason="billing_question",
            )
        if tokens & CLINICAL_TERMS:
            return SmsIntentResult(
                "clinical_question",
                outcome="staff_handoff_required",
                handoff_reason="clinical_question",
            )

        return SmsIntentResult("free_text", outcome="staff_handoff_required", handoff_reason="free_text")


def parse_sms_intent(body: str | None) -> SmsIntentResult:
    return SmsIntentParser().parse(body)
