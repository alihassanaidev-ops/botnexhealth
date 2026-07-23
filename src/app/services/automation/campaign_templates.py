"""Dental-specific campaign template definitions.

Each template carries a normal executable WorkflowDefinition plus product
metadata used by the template picker, guided setup, launch checklist, and future
analytics/audience work. Voice definitions use a non-executable placeholder that
the instantiate endpoint must replace with a clinic-specific Retell agent ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import re
from typing import Any

VOICE_AGENT_PLACEHOLDER = "__SELECT_OUTBOUND_VOICE_AGENT__"
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@dataclass(frozen=True)
class TemplateFrequencyCap:
    max_per_day: int = 1
    max_per_rolling_7_days: int = 3


@dataclass(frozen=True)
class CampaignTemplateMetadata:
    category: str
    goal: str
    outcome_labels: list[str]
    supported_channels: list[str]
    required_readiness_checks: list[str]
    required_merge_fields: list[str]
    default_compliance_content_class: str
    default_audience: str
    default_eligibility_rules: list[str]
    default_frequency_cap: TemplateFrequencyCap
    default_staff_handoff_reason: str | None
    analytics_outcome_map: dict[str, str]
    sample_preview_context: dict[str, Any]
    setup_fields: list[dict[str, Any]] = field(default_factory=list)
    copy_variants: list[dict[str, str]] = field(default_factory=list)
    pms_capability_requirements: list[str] = field(default_factory=list)


@dataclass
class CampaignTemplate:
    id: str
    name: str
    description: str
    trigger_type: str
    definition: dict[str, Any]
    metadata: CampaignTemplateMetadata
    tags: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.metadata.category


_STANDARD_FREQUENCY_CAP = TemplateFrequencyCap()


def template_tokens(definition: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for node in definition.get("nodes", []):
        if not isinstance(node, dict):
            continue
        for key in ("body_template", "subject_template"):
            value = node.get(key)
            if isinstance(value, str):
                tokens.extend(match.group(1) for match in _TOKEN_RE.finditer(value))
    return list(dict.fromkeys(tokens))


def instantiate_definition(template: CampaignTemplate, *, voice_agent_id: str | None = None) -> dict[str, Any]:
    """Return a clone-ready definition with setup-time substitutions applied."""
    definition = copy.deepcopy(template.definition)
    requires_voice = any(
        node.get("type") == "send_voice" and node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER
        for node in definition.get("nodes", [])
        if isinstance(node, dict)
    )
    if requires_voice:
        if not voice_agent_id or not voice_agent_id.strip():
            raise ValueError("voice_agent_id is required for this template")
        for node in definition.get("nodes", []):
            if isinstance(node, dict) and node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER:
                node["retell_agent_id"] = voice_agent_id.strip()
    return definition


def _metadata(
    *,
    category: str,
    goal: str,
    outcome_labels: list[str],
    supported_channels: list[str],
    required_readiness_checks: list[str],
    required_merge_fields: list[str],
    content_class: str,
    audience: str,
    eligibility: list[str],
    handoff_reason: str | None,
    analytics: dict[str, str],
    sample_context: dict[str, Any],
    setup_fields: list[dict[str, Any]] | None = None,
    copy_variants: list[dict[str, str]] | None = None,
    pms_capabilities: list[str] | None = None,
) -> CampaignTemplateMetadata:
    base_setup = [
        {
            "id": "location_id",
            "label": "Location",
            "type": "location",
            "required": True,
        },
        {
            "id": "audience_source",
            "label": "Audience source",
            "type": "select",
            "default": audience,
            "options": [audience],
        },
        {
            "id": "channel_sequence",
            "label": "Channel sequence",
            "type": "select",
            "default": " -> ".join(ch.upper() for ch in supported_channels),
            "options": [" -> ".join(ch.upper() for ch in supported_channels)],
        },
        {
            "id": "send_timing",
            "label": "Send timing",
            "type": "text",
            "default": goal,
        },
        {
            "id": "staff_handoff_behavior",
            "label": "Staff handoff behavior",
            "type": "select",
            "default": handoff_reason or "Monitor campaign operations",
            "options": [handoff_reason or "Monitor campaign operations"],
        },
    ]
    return CampaignTemplateMetadata(
        category=category,
        goal=goal,
        outcome_labels=outcome_labels,
        supported_channels=supported_channels,
        required_readiness_checks=required_readiness_checks,
        required_merge_fields=required_merge_fields,
        default_compliance_content_class=content_class,
        default_audience=audience,
        default_eligibility_rules=eligibility,
        default_frequency_cap=_STANDARD_FREQUENCY_CAP,
        default_staff_handoff_reason=handoff_reason,
        analytics_outcome_map=analytics,
        sample_preview_context=sample_context,
        setup_fields=base_setup + (setup_fields or []),
        copy_variants=copy_variants or [],
        pms_capability_requirements=pms_capabilities or [],
    )


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_APPOINTMENT_REMINDER_24H: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "appointment_offset", "offset_hours": -24},
    "entry_node_id": "sms-reminder",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-reminder",
            "body_template": (
                "Hi {{patient_first_name}}, reminder from {{clinic_name}}: your appointment "
                "is {{appointment_date}} at {{appointment_time}} with {{provider_name}}. "
                "Call {{location_phone}} with questions. Reply STOP to opt out."
            ),
            "next_node_id": "exit-sent",
        },
        {"type": "exit", "id": "exit-sent", "outcome": "reminder_sent"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_APPOINTMENT_CONFIRMATION_48H: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "appointment_offset", "offset_hours": -48},
    "entry_node_id": "sms-confirm",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-confirm",
            "body_template": (
                "Hi {{patient_first_name}}, please confirm your {{clinic_name}} appointment "
                "on {{appointment_date}} at {{appointment_time}}. Reply YES to confirm. "
                "Reply STOP to opt out."
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
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_RECALL_SMS_6MONTH: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "recall_scan", "recall_interval_months": 6},
    "entry_node_id": "sms-recall",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-recall",
            "body_template": (
                "Hi {{patient_first_name}}, {{clinic_name}} shows you are due for routine "
                "hygiene care around {{recall_due_date}}. Book here: {{booking_link}} or "
                "call {{location_phone}}. Reply STOP to opt out."
            ),
            "next_node_id": "exit-sent",
        },
        {"type": "exit", "id": "exit-sent", "outcome": "recall_sent"},
    ],
    "compliance": {"content_class": "recall", "consent_required": True},
}

_REACTIVATION_SMS_EMAIL_18MONTH: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "recall_scan", "recall_interval_months": 18},
    "entry_node_id": "sms-reactivation",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-reactivation",
            "body_template": (
                "Hi {{patient_first_name}}, {{clinic_name}} would like to help you get back "
                "on the schedule for routine dental care. Book here: {{booking_link}} or "
                "call {{location_phone}}. Reply STOP to opt out."
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
                "Hi {{patient_first_name}},\n\n{{clinic_name}} would like to help you get "
                "back on the schedule for routine dental care. You can book online at "
                "{{booking_link}} or call {{location_phone}}.\n\nTake care,\n{{clinic_name}}"
            ),
            "next_node_id": "exit-emailed",
        },
        {"type": "exit", "id": "exit-booked", "outcome": "booked"},
        {"type": "exit", "id": "exit-emailed", "outcome": "email_sent"},
    ],
    "compliance": {"content_class": "recall", "consent_required": True},
}

_NO_SHOW_RECOVERY: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "appointment_offset", "offset_hours": 2},
    "entry_node_id": "check-missed",
    "nodes": [
        {
            "type": "condition",
            "id": "check-missed",
            "rules": [{"field": "appointment_status", "op": "eq", "value": "missed"}],
            "true_next_node_id": "sms-rebook",
            "false_next_node_id": "exit-not-missed",
        },
        {
            "type": "send_sms",
            "id": "sms-rebook",
            "body_template": (
                "Hi {{patient_first_name}}, we missed you at {{clinic_name}} today. "
                "Use {{reschedule_link}} or call {{location_phone}} and we can find a new time. "
                "Reply STOP to opt out."
            ),
            "next_node_id": "wait-booking",
        },
        {
            "type": "wait",
            "id": "wait-booking",
            "delay": {"delay_type": "duration", "duration_seconds": 86400},
            "next_node_id": "check-booked",
        },
        {
            "type": "condition",
            "id": "check-booked",
            "rules": [{"field": "appointment_booked", "op": "eq", "value": True}],
            "true_next_node_id": "exit-booked",
            "false_next_node_id": "exit-handoff",
        },
        {"type": "exit", "id": "exit-booked", "outcome": "booked"},
        {"type": "exit", "id": "exit-handoff", "outcome": "handoff"},
        {"type": "exit", "id": "exit-not-missed", "outcome": "not_applicable"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_CANCELLATION_REBOOKING: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "appointment_offset", "offset_hours": 1},
    "entry_node_id": "check-cancelled",
    "nodes": [
        {
            "type": "condition",
            "id": "check-cancelled",
            "rules": [{"field": "appointment_status", "op": "eq", "value": "cancelled"}],
            "true_next_node_id": "sms-rebook",
            "false_next_node_id": "exit-not-cancelled",
        },
        {
            "type": "send_sms",
            "id": "sms-rebook",
            "body_template": (
                "Hi {{patient_first_name}}, {{clinic_name}} can help reschedule your "
                "cancelled appointment. Pick a new time here: {{reschedule_link}} or call "
                "{{location_phone}}. Reply STOP to opt out."
            ),
            "next_node_id": "exit-rebooking-sent",
        },
        {"type": "exit", "id": "exit-rebooking-sent", "outcome": "rebooking_link_sent"},
        {"type": "exit", "id": "exit-not-cancelled", "outcome": "not_applicable"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_CALLBACK_AUTOMATION: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "callback_requested"},
    "entry_node_id": "voice-callback",
    "nodes": [
        {
            "type": "send_voice",
            "id": "voice-callback",
            "retell_agent_id": VOICE_AGENT_PLACEHOLDER,
            "wait_for_outcome": True,
            "max_attempts": 1,
            "next_node_id": "check-call-outcome",
        },
        {
            "type": "condition",
            "id": "check-call-outcome",
            "rules": [{"field": "call_outcome", "op": "in", "value": ["answered", "transferred"]}],
            "true_next_node_id": "exit-handled",
            "false_next_node_id": "exit-handoff",
        },
        {"type": "exit", "id": "exit-handled", "outcome": "answered"},
        {"type": "exit", "id": "exit-handoff", "outcome": "staff_handoff"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_UNSCHEDULED_TREATMENT_FOLLOWUP: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "manual"},
    "entry_node_id": "sms-treatment-followup",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-treatment-followup",
            "body_template": (
                "Hi {{patient_first_name}}, {{clinic_name}} is checking in about your "
                "next dental visit. You can schedule here: {{booking_link}} or call "
                "{{location_phone}}. Reply STOP to opt out."
            ),
            "next_node_id": "wait-72h",
        },
        {
            "type": "wait",
            "id": "wait-72h",
            "delay": {"delay_type": "duration", "duration_seconds": 259200},
            "next_node_id": "check-booked",
        },
        {
            "type": "condition",
            "id": "check-booked",
            "rules": [{"field": "appointment_booked", "op": "eq", "value": True}],
            "true_next_node_id": "exit-booked",
            "false_next_node_id": "email-followup",
        },
        {
            "type": "send_email",
            "id": "email-followup",
            "subject_template": "Next visit scheduling with {{clinic_name}}",
            "body_template": (
                "Hi {{patient_first_name}},\n\nOur team is available to help schedule "
                "your next dental visit. Book online at {{booking_link}} or call "
                "{{location_phone}}.\n\n{{clinic_name}}"
            ),
            "next_node_id": "exit-emailed",
        },
        {"type": "exit", "id": "exit-booked", "outcome": "booked"},
        {"type": "exit", "id": "exit-emailed", "outcome": "email_sent"},
    ],
    "compliance": {"content_class": "sales", "consent_required": True},
}

_SURGERY_CONFIRMATION_AND_POST_OP: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {
        "type": "appointment_offset",
        "offset_hours": -24,
        "appointment_type_ids": None,
    },
    "entry_node_id": "voice-preop-confirmation",
    "nodes": [
        {
            "type": "send_voice",
            "id": "voice-preop-confirmation",
            "retell_agent_id": VOICE_AGENT_PLACEHOLDER,
            "wait_for_outcome": True,
            "max_attempts": 1,
            "next_node_id": "check-preop-outcome",
        },
        {
            "type": "condition",
            "id": "check-preop-outcome",
            "logic": "OR",
            "rules": [
                {"field": "call_outcome", "op": "in", "value": ["confirmed", "answered", "booked"]}
            ],
            "true_next_node_id": "mark-confirmed",
            "false_next_node_id": "check-dnc",
        },
        {
            "type": "update_patient_status",
            "id": "mark-confirmed",
            "status": "appointment_confirmed",
            "note_template": "Pre-appointment call outcome: {{call_outcome}}",
            "next_node_id": "wait-post-op",
        },
        {
            "type": "condition",
            "id": "check-dnc",
            "rules": [{"field": "call_outcome", "op": "eq", "value": "do_not_call"}],
            "true_next_node_id": "mark-dnc",
            "false_next_node_id": "mark-followup",
        },
        {
            "type": "update_patient_status",
            "id": "mark-dnc",
            "status": "do_not_call_requested",
            "note_template": "Patient requested no further calls during pre-appointment outreach.",
            "next_node_id": "exit-dnc",
        },
        {
            "type": "update_patient_status",
            "id": "mark-followup",
            "status": "reschedule_or_followup_needed",
            "note_template": "Pre-appointment call needs staff review. Outcome: {{call_outcome}}",
            "next_node_id": "exit-handoff",
        },
        {
            "type": "wait",
            "id": "wait-post-op",
            "delay": {
                "delay_type": "appointment_relative",
                "offset_seconds": 86400,
                "anchor_field": "appointment_at",
            },
            "next_node_id": "voice-post-op",
        },
        {
            "type": "send_voice",
            "id": "voice-post-op",
            "retell_agent_id": VOICE_AGENT_PLACEHOLDER,
            "wait_for_outcome": True,
            "max_attempts": 1,
            "next_node_id": "mark-post-op",
        },
        {
            "type": "update_patient_status",
            "id": "mark-post-op",
            "status": "post_op_complete",
            "note_template": "Post-op call outcome: {{call_outcome}}",
            "next_node_id": "exit-post-op-complete",
        },
        {"type": "exit", "id": "exit-post-op-complete", "outcome": "post_op_complete"},
        {"type": "exit", "id": "exit-handoff", "outcome": "staff_handoff"},
        {"type": "exit", "id": "exit-dnc", "outcome": "do_not_call"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
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
        metadata=_metadata(
            category="appointment_ops",
            goal="Reduce late arrivals and missed appointments one day before the visit.",
            outcome_labels=["reminder_sent"],
            supported_channels=["sms"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "sms", "consent", "quiet_hours"],
            required_merge_fields=["patient_first_name", "clinic_name", "appointment_date", "appointment_time", "provider_name", "location_phone"],
            content_class="transactional_care",
            audience="NexHealth appointments scheduled 24 hours from now",
            eligibility=["future appointment still exists", "patient is not suppressed", "SMS consent exists"],
            handoff_reason=None,
            analytics={"reminder_sent": "sent"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "appointment_date": "July 22, 2026",
                "appointment_time": "2:00 PM",
                "provider_name": "Dr. Smith",
                "location_phone": "(555) 010-2211",
            },
            copy_variants=[
                {"id": "standard", "label": "Standard reminder"},
                {"id": "short", "label": "Short reminder"},
            ],
        ),
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
        metadata=_metadata(
            category="appointment_ops",
            goal="Collect YES confirmations 48 hours before appointments.",
            outcome_labels=["confirmed", "no_response"],
            supported_channels=["sms"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "sms", "consent", "response_handling"],
            required_merge_fields=["patient_first_name", "clinic_name", "appointment_date", "appointment_time"],
            content_class="transactional_care",
            audience="NexHealth appointments scheduled 48 hours from now and not already confirmed",
            eligibility=["future appointment still exists", "patient is not suppressed", "SMS consent exists"],
            handoff_reason="reschedule_requested",
            analytics={"confirmed": "confirmed", "no_response": "no_response"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "appointment_date": "July 22, 2026",
                "appointment_time": "2:00 PM",
            },
            copy_variants=[
                {"id": "yes_only", "label": "YES confirmation"},
                {"id": "link_plus_yes", "label": "Link plus YES"},
            ],
        ),
        tags=["appointment", "confirmation", "sms"],
    ),
    "recall-sms-6month": CampaignTemplate(
        id="recall-sms-6month",
        name="Recall Outreach (6-Month)",
        description="Send an SMS recall message to patients overdue for a 6-month checkup.",
        trigger_type="recall_scan",
        definition=_RECALL_SMS_6MONTH,
        metadata=_metadata(
            category="recall",
            goal="Bring overdue hygiene recall patients back onto the schedule.",
            outcome_labels=["recall_sent", "booked"],
            supported_channels=["sms"],
            required_readiness_checks=["location", "nexhealth_patient_recalls", "sms", "booking_link", "consent"],
            required_merge_fields=["patient_first_name", "clinic_name", "recall_due_date", "booking_link", "location_phone"],
            content_class="recall",
            audience="Patients due or overdue for 6-month hygiene recall with no future appointment",
            eligibility=["PMS supports patient_recalls", "no future appointment", "patient is not suppressed", "SMS consent exists"],
            handoff_reason="patient_asks_for_staff",
            analytics={"recall_sent": "sent", "booked": "booked"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "recall_due_date": "August 15, 2026",
                "booking_link": "https://book.example.com/r/jordan",
                "location_phone": "(555) 010-2211",
            },
            pms_capabilities=["patient_recalls"],
        ),
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
        metadata=_metadata(
            category="reactivation",
            goal="Re-engage lapsed patients who have not booked in 18 months.",
            outcome_labels=["booked", "email_sent"],
            supported_channels=["sms", "email"],
            required_readiness_checks=["location", "nexhealth_patient_recalls", "sms", "email", "booking_link", "consent"],
            required_merge_fields=["patient_first_name", "clinic_name", "booking_link", "location_phone"],
            content_class="recall",
            audience="Patients inactive for 18 months with no future appointment",
            eligibility=["PMS supports patient_recalls", "no future appointment", "patient is not suppressed", "SMS/email consent exists"],
            handoff_reason="patient_asks_for_staff",
            analytics={"booked": "booked", "email_sent": "sent"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "booking_link": "https://book.example.com/r/jordan",
                "location_phone": "(555) 010-2211",
            },
            pms_capabilities=["patient_recalls"],
        ),
        tags=["reactivation", "sms", "email"],
    ),
    "no-show-recovery": CampaignTemplate(
        id="no-show-recovery",
        name="No-Show Recovery",
        description="Send a same-day rebooking link after a missed appointment and flag no booking for staff follow-up.",
        trigger_type="appointment_offset",
        definition=_NO_SHOW_RECOVERY,
        metadata=_metadata(
            category="appointment_ops",
            goal="Recover missed appointments before the schedule gap becomes permanent.",
            outcome_labels=["booked", "handoff", "not_applicable"],
            supported_channels=["sms"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "sms", "reschedule_link", "consent"],
            required_merge_fields=["patient_first_name", "clinic_name", "reschedule_link", "location_phone"],
            content_class="transactional_care",
            audience="Appointments marked missed/no-show by NexHealth",
            eligibility=["appointment is still marked missed", "patient is not suppressed", "SMS consent exists"],
            handoff_reason="failed_booking",
            analytics={"booked": "booked", "handoff": "handoff", "not_applicable": "skipped"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "reschedule_link": "https://book.example.com/r/abc123",
                "location_phone": "(555) 010-2211",
                "appointment_status": "missed",
            },
        ),
        tags=["appointment", "no-show", "sms", "handoff"],
    ),
    "cancellation-rebooking": CampaignTemplate(
        id="cancellation-rebooking",
        name="Cancellation Rebooking",
        description="Offer a rebooking path after a cancelled appointment is observed.",
        trigger_type="appointment_offset",
        definition=_CANCELLATION_REBOOKING,
        metadata=_metadata(
            category="appointment_ops",
            goal="Turn cancellations into new bookings quickly.",
            outcome_labels=["rebooking_link_sent", "not_applicable"],
            supported_channels=["sms"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "sms", "reschedule_link", "consent"],
            required_merge_fields=["patient_first_name", "clinic_name", "reschedule_link", "location_phone"],
            content_class="transactional_care",
            audience="Appointments marked cancelled by NexHealth",
            eligibility=["appointment is still cancelled", "patient is not suppressed", "SMS consent exists"],
            handoff_reason="reschedule_requested",
            analytics={"rebooking_link_sent": "sent", "not_applicable": "skipped"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "reschedule_link": "https://book.example.com/r/abc123",
                "location_phone": "(555) 010-2211",
                "appointment_status": "cancelled",
            },
        ),
        tags=["appointment", "cancellation", "sms"],
    ),
    "surgery-confirmation-post-op": CampaignTemplate(
        id="surgery-confirmation-post-op",
        name="Surgery Confirmation + Post-Op",
        description=(
            "Call patients before major appointments to confirm attendance, then "
            "call one day after the appointment for post-op follow-up."
        ),
        trigger_type="appointment_offset",
        definition=_SURGERY_CONFIRMATION_AND_POST_OP,
        metadata=_metadata(
            category="appointment_ops",
            goal="Confirm major appointments before the visit and complete next-day post-op follow-up.",
            outcome_labels=["post_op_complete", "staff_handoff", "do_not_call"],
            supported_channels=["voice"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "voice", "consent", "quiet_hours"],
            required_merge_fields=["patient_first_name", "clinic_name", "appointment_date", "appointment_time", "appointment_type"],
            content_class="transactional_care",
            audience="Major/surgical appointment types selected by the clinic",
            eligibility=[
                "appointment type is selected for this workflow",
                "future appointment still exists",
                "patient is not suppressed",
                "voice consent exists",
            ],
            handoff_reason="reschedule_or_followup_needed",
            analytics={
                "post_op_complete": "completed",
                "staff_handoff": "handoff",
                "do_not_call": "opt_out",
            },
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "appointment_date": "July 22, 2026",
                "appointment_time": "2:00 PM",
                "appointment_type": "Implant Surgery",
                "call_outcome": "confirmed",
            },
            setup_fields=[
                {
                    "id": "appointment_type_ids",
                    "label": "Major appointment types",
                    "type": "appointment_type_multiselect",
                    "required": True,
                }
            ],
        ),
        tags=["appointment", "surgery", "voice", "post-op"],
    ),
    "callback-automation": CampaignTemplate(
        id="callback-automation",
        name="Callback Automation",
        description="Place an AI voice callback for patients who requested a return call and route unresolved calls to staff.",
        trigger_type="callback_requested",
        definition=_CALLBACK_AUTOMATION,
        metadata=_metadata(
            category="callback",
            goal="Respond to callback requests with a configured AI voice profile.",
            outcome_labels=["answered", "booked", "transferred", "staff_handoff", "unreachable", "do_not_call"],
            supported_channels=["voice"],
            required_readiness_checks=["location", "callback_queue_source", "outbound_voice_profile", "voice_consent", "voice_outcome_wait", "staff_handoff", "quiet_hours"],
            required_merge_fields=["callback_requested_at"],
            content_class="transactional_care",
            audience="Inbound calls classified as needing callback",
            eligibility=["active outbound voice profile", "voice consent exists", "patient is not suppressed"],
            handoff_reason="ambiguous_voice_outcome",
            analytics={
                "callback_requested": "callbacks_automated",
                "answered": "answered",
                "booked": "booked",
                "transferred": "transferred",
                "staff_handoff": "staff_handoff",
                "no_answer": "unreachable",
                "busy": "unreachable",
                "failed": "unreachable",
                "do_not_call": "do_not_call",
            },
            sample_context={
                "callback_requested_at": "July 18, 2026 at 10:30 AM",
                "callback_reason": "Reschedule request",
                "preferred_callback_time": "Today after 3:00 PM",
            },
            setup_fields=[
                {
                    "id": "voice_agent_id",
                    "label": "Voice profile",
                    "type": "text",
                    "required": True,
                    "placeholder": "Retell agent ID",
                }
            ],
        ),
        tags=["callback", "voice", "handoff"],
    ),
    "unscheduled-treatment-followup": CampaignTemplate(
        id="unscheduled-treatment-followup",
        name="Unscheduled Treatment Follow-Up",
        description="Follow up with patients who need a next visit scheduled without exposing treatment details in copy.",
        trigger_type="manual",
        definition=_UNSCHEDULED_TREATMENT_FOLLOWUP,
        metadata=_metadata(
            category="treatment",
            goal="Help patients schedule their next dental visit after unscheduled treatment planning.",
            outcome_labels=["booked", "email_sent"],
            supported_channels=["sms", "email"],
            required_readiness_checks=["location", "pms_treatment_plans", "sms", "email", "booking_link", "express_consent"],
            required_merge_fields=["patient_first_name", "clinic_name", "booking_link", "location_phone"],
            content_class="sales",
            audience="Manual or PMS-gated treatment-plan audience selected after preview",
            eligibility=["PMS supports treatment_plans when automated", "patient is not suppressed", "express SMS/email consent exists"],
            handoff_reason="patient_asks_for_staff",
            analytics={"booked": "booked", "email_sent": "sent"},
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "booking_link": "https://book.example.com/r/jordan",
                "location_phone": "(555) 010-2211",
            },
            pms_capabilities=["treatment_plans"],
        ),
        tags=["treatment", "sms", "email"],
    ),
}


def get_template(template_id: str) -> CampaignTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[CampaignTemplate]:
    return list(TEMPLATES.values())
