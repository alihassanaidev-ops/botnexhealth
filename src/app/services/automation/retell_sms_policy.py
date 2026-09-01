"""Platform-owned policy for Retell-generated SMS conversations.

Workflow authors select an agent profile and the next step. Lifecycle, failure,
message-size, and data-disclosure safeguards stay behind this module's small
interface so every workflow gets the same reviewed behavior.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetellSmsPlatformPolicy:
    inactivity_timeout_seconds: int = 3600
    max_duration_seconds: int = 86400
    max_patient_turns: int = 12
    max_response_segments: int = 3
    respect_quiet_hours: bool = True


RETELL_SMS_POLICY = RetellSmsPlatformPolicy()

# Provider-neutral fields resolved by MergeContextBuilder. Missing values are
# omitted, so one Retell profile works for both NexHealth and GoTracker runs.
AUTOMATIC_RETELL_SMS_VARIABLES: tuple[str, ...] = (
    "patient_first_name",
    "patient_preferred_language",
    "clinic_name",
    "location_name",
    "location_phone",
    "appointment_date",
    "appointment_time",
    "appointment_datetime",
    "appointment_reason",
    "appointment_status",
    "appointment_type",
    "appointment_type_name",
    "provider_name",
    "booking_link",
    "registration_link",
    "confirmation_link",
    "reschedule_link",
    "recall_due_date",
    "callback_requested_at",
    "preferred_callback_time",
    "enquiry_source",
    "enquiry_status",
    "matched_existing_contact",
    "sms_reply_intent",
)

# Retell agents may collect this field before ending the chat. It is surfaced as
# ``retell_sms_agent_outcome`` for a downstream Condition node.
RETELL_SMS_AGENT_OUTCOME_VARIABLE = "conversation_outcome"
