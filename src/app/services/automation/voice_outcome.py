"""Map a Retell call's raw end-state to a normalized workflow outcome (Plan 03).

`disconnection_reason` values are Retell's documented enum (verified 2026-07-04,
docs.retellai.com/reliability/debug-call-disconnect). The normalized `call_outcome`
is written into the run context so a following ConditionNode can branch
(answered / no_answer / busy / voicemail / failed / transferred / unknown).

NOTE: "booked" is NOT derivable from disconnection_reason — a connected call ends
with user/agent hangup ("answered"); whether an appointment was booked comes from
the post-call analysis (custom_analysis_data), a follow-up enhancement.
"""

from __future__ import annotations

# Retell disconnection_reason → normalized call_outcome
_REASON_MAP: dict[str, str] = {
    "dial_no_answer": "no_answer",
    "dial_busy": "busy",
    "voicemail_reached": "voicemail",
    "ivr_reached": "ivr",
    "user_hangup": "answered",
    "agent_hangup": "answered",
    "call_transfer": "transferred",
    "call_take_over": "transferred",
    "user_declined": "declined",
    "dial_failed": "failed",
    "invalid_destination": "failed",
    "marked_as_spam": "failed",
    "sip_routing_error": "failed",
    "telephony_provider_unavailable": "failed",
    "telephony_provider_permission_denied": "failed",
}

# The full set of normalized outcomes a ConditionNode may branch on.
VOICE_OUTCOMES = frozenset(_REASON_MAP.values()) | {"unknown", "timeout"}

# Canonical Retell post-call analysis fields that may drive executable workflow
# behavior. Keep this allowlist narrow: arbitrary custom analysis must never be
# copied into a run definition's context and accidentally become an action input.
WORKFLOW_OUTCOME_CONTEXT_FIELDS = frozenset(
    {
        "callback_at",
        "reschedule_start_time",
        "reschedule_end_time",
    }
)


def extract_workflow_outcome_context(custom_analysis: object) -> dict[str, str]:
    if not isinstance(custom_analysis, dict):
        return {}
    context: dict[str, str] = {}
    for field in WORKFLOW_OUTCOME_CONTEXT_FIELDS:
        value = custom_analysis.get(field)
        if isinstance(value, str) and value.strip():
            context[field] = value.strip()
    return context


def map_disconnection_reason(
    disconnection_reason: str | None, call_status: str | None = None
) -> str:
    """Return the normalized call_outcome for a Retell call_analyzed event.

    Unknown/missing reasons fall back to "unknown" so a downstream branch can still
    route them (e.g. treat as no_answer/retry) rather than silently stalling.
    """
    if disconnection_reason:
        mapped = _REASON_MAP.get(disconnection_reason)
        if mapped:
            return mapped
    return "unknown"
