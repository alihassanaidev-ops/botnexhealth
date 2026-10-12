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
    # Ended by the agent's own limits after a conversation: the post-call tags
    # say what happened.
    "max_duration_reached": "answered",
    # The patient confirmed a do-not-call request; see campaign_call_outcome.
    "user_requested_dnc": "answered",
    # A failed transfer still means the patient was on the line and wanted a
    # person; see campaign_call_outcome.
    "transfer_cancelled": "answered",
    # Silence: nobody engaged, so treat it like a missed call and retry.
    "inactivity": "no_answer",
    "call_transfer": "transferred",
    "transfer_bridged": "transferred",
    "call_take_over": "transferred",
    "user_declined": "declined",
    "dial_failed": "failed",
    "invalid_destination": "failed",
    "marked_as_spam": "failed",
    "network_blocked": "failed",
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


# Post-call "Call Status" tags (normalized, see post_call_service) → the
# campaign outcome a recall route branches on. Highest-priority tag wins.
_TAG_OUTCOMES: tuple[tuple[str, frozenset[str]], ...] = (
    ("booked", frozenset({"appointment_booked", "appointment_rescheduled"})),
    (
        "callback_requested",
        frozenset({"needs_callback", "needs_booking", "needs_reschedule"}),
    ),
    ("transferred", frozenset({"transferred"})),
    (
        "staff_handoff",
        frozenset(
            {
                "complaint",
                "emergency",
                "financial_inquiry",
                "insurance_and_billing",
                "insurance_unverified",
                "appointment_cancelled",
                "needs_cancellation",
            }
        ),
    ),
    ("contacted", frozenset({"no_action_needed", "faq_handled", "insurance_verified"})),
)


def _call_status_tags(custom: dict) -> set[str]:
    from src.app.services.post_call_service import RETELL_STATUS_MAP

    raw = custom.get("Call Status")
    if not isinstance(raw, str):
        return set()
    tags = set()
    for token in raw.split(","):
        mapped = RETELL_STATUS_MAP.get(token.strip().lower())
        if mapped:
            tags.add(mapped)
    return tags


def campaign_call_outcome(
    disconnection_reason: str | None,
    call_status: str | None,
    custom_analysis: object,
) -> str:
    """The outcome an outbound campaign run branches on after a Retell call.

    A call that did not connect is described by how it ended (no_answer,
    voicemail, failed, ...). A call where the patient talked to the agent is
    described by the agent's post-call "Call Status" tags, the same tags
    inbound calls are labeled with. A do-not-call request wins over everything.
    """
    from src.app.services.automation.voice_optout_service import detect_voice_optout

    custom = custom_analysis if isinstance(custom_analysis, dict) else {}
    if disconnection_reason == "user_requested_dnc" or detect_voice_optout(custom):
        return "do_not_call"
    explicit = custom.get("call_outcome")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if disconnection_reason == "transfer_cancelled":
        return "callback_requested"
    mapped = map_disconnection_reason(disconnection_reason, call_status)
    if mapped != "answered":
        return mapped
    tags = _call_status_tags(custom)
    for outcome, statuses in _TAG_OUTCOMES:
        if tags & statuses:
            return outcome
    return "answered"
