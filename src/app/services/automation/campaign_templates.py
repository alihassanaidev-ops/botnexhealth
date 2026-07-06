"""Pre-built campaign template definitions for the outbound engagement engine.

Each template is a valid WorkflowDefinition dict ready to be instantiated as a
draft workflow. Templates that include SendVoiceNode are intentionally excluded
from this library because voice requires a clinic-specific Retell agent ID that
cannot be pre-configured as a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CampaignTemplate:
    id: str
    name: str
    description: str
    trigger_type: str
    definition: dict[str, Any]
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_APPOINTMENT_REMINDER_24H: dict[str, Any] = {
    "trigger": {"type": "appointment_offset", "offset_hours": -24},
    "entry_node_id": "sms-reminder",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-reminder",
            "body_template": (
                "Hi {{patient_first_name}}, this is a reminder that you have an "
                "appointment tomorrow. Reply STOP to opt out."
            ),
            "next_node_id": "exit-sent",
        },
        {"type": "exit", "id": "exit-sent", "outcome": "reminder_sent"},
    ],
}

_APPOINTMENT_CONFIRMATION_48H: dict[str, Any] = {
    "trigger": {"type": "appointment_offset", "offset_hours": -48},
    "entry_node_id": "sms-confirm",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-confirm",
            "body_template": (
                "Hi {{patient_first_name}}, please confirm your upcoming appointment. "
                "Reply YES to confirm. Reply STOP to opt out."
            ),
            "next_node_id": "wait-response",
        },
        {
            "type": "wait",
            "id": "wait-response",
            "delay": {"delay_type": "duration", "duration_seconds": 7200},
            "next_node_id": "check-confirmed",
        },
        {
            "type": "condition",
            "id": "check-confirmed",
            "rules": [
                {"field": "appointment_status", "op": "eq", "value": "confirmed"}
            ],
            "true_next_node_id": "exit-confirmed",
            "false_next_node_id": "exit-no-response",
        },
        {"type": "exit", "id": "exit-confirmed", "outcome": "confirmed"},
        {"type": "exit", "id": "exit-no-response", "outcome": "no_response"},
    ],
}

_RECALL_SMS_6MONTH: dict[str, Any] = {
    "trigger": {"type": "recall_scan", "recall_interval_months": 6},
    "entry_node_id": "sms-recall",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-recall",
            "body_template": (
                "Hi {{patient_first_name}}, it's time for your 6-month checkup! "
                "Book online or call us to schedule. Reply STOP to opt out."
            ),
            "next_node_id": "exit-sent",
        },
        {"type": "exit", "id": "exit-sent", "outcome": "recall_sent"},
    ],
}

_REACTIVATION_SMS_EMAIL_18MONTH: dict[str, Any] = {
    "trigger": {"type": "recall_scan", "recall_interval_months": 18},
    "entry_node_id": "sms-reactivation",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-reactivation",
            "body_template": (
                "Hi {{patient_first_name}}, we miss you! It's been a while since your "
                "last visit. Book your next appointment today. Reply STOP to opt out."
            ),
            "next_node_id": "wait-48h",
        },
        {
            "type": "wait",
            "id": "wait-48h",
            "delay": {"delay_type": "duration", "duration_seconds": 172800},
            "next_node_id": "check-booked",
        },
        {
            "type": "condition",
            "id": "check-booked",
            "rules": [
                {"field": "appointment_booked", "op": "eq", "value": True}
            ],
            "true_next_node_id": "exit-booked",
            "false_next_node_id": "email-followup",
        },
        {
            "type": "send_email",
            "id": "email-followup",
            "subject_template": "We'd love to see you again, {{patient_first_name}}",
            "body_template": (
                "Hi {{patient_first_name}},\n\nWe noticed it's been a while since your "
                "last visit and wanted to reach out. Our team is here whenever you're "
                "ready to schedule your next appointment.\n\nTake care,\n{{clinic_name}}"
            ),
            "next_node_id": "exit-emailed",
        },
        {"type": "exit", "id": "exit-booked", "outcome": "booked"},
        {"type": "exit", "id": "exit-emailed", "outcome": "email_sent"},
    ],
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, CampaignTemplate] = {
    "appointment-reminder-24h": CampaignTemplate(
        id="appointment-reminder-24h",
        name="Appointment Reminder (24h)",
        description="Send an SMS reminder 24 hours before a scheduled appointment.",
        trigger_type="appointment_offset",
        definition=_APPOINTMENT_REMINDER_24H,
        tags=["appointment", "reminder", "sms"],
    ),
    "appointment-confirmation-48h": CampaignTemplate(
        id="appointment-confirmation-48h",
        name="Appointment Confirmation (48h)",
        description=(
            "Send an SMS confirmation request 48 hours before the appointment "
            "and check for a response after 2 hours."
        ),
        trigger_type="appointment_offset",
        definition=_APPOINTMENT_CONFIRMATION_48H,
        tags=["appointment", "confirmation", "sms"],
    ),
    "recall-sms-6month": CampaignTemplate(
        id="recall-sms-6month",
        name="Recall Outreach (6-Month)",
        description="Send an SMS recall message to patients overdue for a 6-month checkup.",
        trigger_type="recall_scan",
        definition=_RECALL_SMS_6MONTH,
        tags=["recall", "sms"],
    ),
    "reactivation-sms-email-18month": CampaignTemplate(
        id="reactivation-sms-email-18month",
        name="Reactivation Campaign (18-Month)",
        description=(
            "Re-engage patients inactive for 18 months with an SMS outreach "
            "followed by an email if no appointment is booked within 48 hours."
        ),
        trigger_type="recall_scan",
        definition=_REACTIVATION_SMS_EMAIL_18MONTH,
        tags=["reactivation", "sms", "email"],
    ),
}


def get_template(template_id: str) -> CampaignTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[CampaignTemplate]:
    return list(TEMPLATES.values())
