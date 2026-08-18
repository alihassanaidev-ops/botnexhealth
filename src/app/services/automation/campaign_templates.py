"""Dental-specific campaign template definitions.

Each template carries a normal executable WorkflowDefinition plus product
metadata used by the template picker, guided setup, launch checklist, and future
analytics/audience work. Voice definitions use a non-executable placeholder that
the instantiate endpoint must replace with a location-specific outbound voice
profile id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
import re
from typing import Any

VOICE_AGENT_PLACEHOLDER = "__SELECT_OUTBOUND_VOICE_AGENT__"
VOICE_PROFILE_PLACEHOLDER = "__SELECT_OUTBOUND_VOICE_PROFILE__"
APPOINTMENT_REASONS_PLACEHOLDER = "__SELECT_APPOINTMENT_REASONS__"
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


def instantiate_definition(
    template: CampaignTemplate,
    *,
    voice_profile_id: str | None = None,
    voice_agent_id: str | None = None,
    setup_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a clone-ready definition with setup-time substitutions applied."""
    definition = copy.deepcopy(template.definition)
    # Compliance classification is owned by Retell. Keep template metadata
    # compatibility elsewhere, but never copy the legacy workflow-level block
    # into a newly instantiated outbound workflow.
    definition.pop("compliance", None)
    setup_options = setup_options or {}
    requires_voice = any(
        node.get("type") == "send_voice"
        and (
            node.get("voice_profile_id") == VOICE_PROFILE_PLACEHOLDER
            or node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER
        )
        for node in definition.get("nodes", [])
        if isinstance(node, dict)
    )
    if requires_voice:
        selected_profile_id = (voice_profile_id or voice_agent_id or "").strip()
        if not selected_profile_id:
            raise ValueError("voice_profile_id is required for this template")
        for node in definition.get("nodes", []):
            if not isinstance(node, dict):
                continue
            if (
                node.get("voice_profile_id") == VOICE_PROFILE_PLACEHOLDER
                or node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER
            ):
                node["voice_profile_id"] = selected_profile_id
                if node.get("retell_agent_id") == VOICE_AGENT_PLACEHOLDER:
                    node["retell_agent_id"] = ""

    _apply_required_setup_fields(template, definition, setup_options)
    return definition


def _apply_required_setup_fields(
    template: CampaignTemplate,
    definition: dict[str, Any],
    setup_options: dict[str, Any],
) -> None:
    """Apply setup fields that affect executable workflow behavior."""
    fields = template.metadata.setup_fields
    for setup_field in fields:
        field_id = setup_field.get("id")
        if field_id == "appointment_type_ids":
            continue
        if field_id == "appointment_reasons":
            reasons = _string_list(setup_options.get(field_id))
            if setup_field.get("required") and not reasons:
                raise ValueError("appointment_reasons must contain at least one GoTracker reason")
            node = _node_by_id(definition, "check-eligible-reason")
            if reasons and node:
                for rule in node.get("rules", []):
                    if isinstance(rule, dict) and rule.get("field") == "appointment_reason":
                        rule["value"] = reasons
            continue
        if field_id == "call_offset_hours_before":
            hours = _positive_number(
                setup_options.get(field_id, setup_field.get("default", 24)),
                field_id,
                integer=True,
                allow_zero=True,
            )
            definition["trigger"]["offset_hours"] = -int(hours)
            continue
        if field_id in {"retry_delay_1_hours", "retry_delay_2_hours"}:
            hours = _positive_number(
                setup_options.get(field_id, setup_field.get("default", 5)),
                field_id,
            )
            wait_id = "wait-retry-1" if field_id == "retry_delay_1_hours" else "wait-retry-2"
            node = _node_by_id(definition, wait_id)
            if node:
                node["delay"] = {
                    "delay_type": "duration",
                    "duration_seconds": int(hours * 60 * 60),
                }
            continue
        if field_id == "patient_voice_cooldown_hours":
            hours = _positive_number(
                setup_options.get(field_id, setup_field.get("default", 24)),
                field_id,
                integer=True,
                allow_zero=True,
            )
            for node in definition.get("nodes", []):
                if isinstance(node, dict) and node.get("type") == "send_voice":
                    node["patient_voice_cooldown_hours"] = int(hours)
            continue
        if field_id == "post_op_reasons":
            reasons = _string_list(setup_options.get(field_id))
            if setup_field.get("required") and not reasons:
                raise ValueError("post_op_reasons must contain at least one GoTracker reason")
            node = _node_by_id(definition, "check-post-op-eligible-reason")
            if node:
                node["rules"][0]["value"] = reasons
            continue
        if field_id == "post_op_delay_hours":
            hours = _positive_number(
                setup_options.get(field_id, setup_field.get("default", 24)),
                field_id,
                integer=True,
                allow_zero=True,
            )
            node = _node_by_id(definition, "wait-post-op")
            if node:
                node["delay"]["offset_seconds"] = int(hours * 60 * 60)
            continue
        if field_id == "post_op_latest_call_hours":
            hours = _positive_number(
                setup_options.get(field_id, setup_field.get("default", 72)),
                field_id,
                integer=True,
            )
            definition["trigger"]["max_followup_delay_hours"] = int(hours)
            continue

    # A call cannot be both scheduled after completion and forbidden before it
    # becomes eligible. Keep the setup error local and understandable.
    if _node_by_id(definition, "wait-post-op") is not None:
        delay = _positive_number(
            setup_options.get("post_op_delay_hours", 24),
            "post_op_delay_hours",
            integer=True,
            allow_zero=True,
        )
        latest = _positive_number(
            setup_options.get("post_op_latest_call_hours", 72),
            "post_op_latest_call_hours",
            integer=True,
        )
        if latest < delay:
            raise ValueError(
                "post_op_latest_call_hours must be at least post_op_delay_hours"
            )


def _node_by_id(definition: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return next(
        (
            node
            for node in definition.get("nodes", [])
            if isinstance(node, dict) and node.get("id") == node_id
        ),
        None,
    )


def _positive_number(
    value: Any,
    field_id: str,
    *,
    integer: bool = False,
    allow_zero: bool = False,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_id} must be a positive number") from exc
    below_minimum = parsed < 0 if allow_zero else parsed <= 0
    if not math.isfinite(parsed) or below_minimum or (integer and not parsed.is_integer()):
        if allow_zero:
            qualifier = "non-negative whole number" if integer else "non-negative number"
        else:
            qualifier = "positive whole number" if integer else "positive number"
        raise ValueError(f"{field_id} must be a {qualifier}")
    return parsed


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        return []
    return list(dict.fromkeys(str(part).strip() for part in parts if str(part).strip()))


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
    frequency_cap: TemplateFrequencyCap = _STANDARD_FREQUENCY_CAP,
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
        default_frequency_cap=frequency_cap,
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
            "retell_agent_id": "",
            "voice_profile_id": VOICE_PROFILE_PLACEHOLDER,
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

def _preappointment_attempt_nodes(attempt: int) -> list[dict[str, Any]]:
    """Build one explicit patient-contact attempt and its outcome router."""
    final_attempt = attempt == 3
    suffix = str(attempt)
    callback_target = (
        "mark-callback-after-max" if final_attempt else f"check-callback-time-{suffix}"
    )
    unreachable_target = "mark-max-attempts" if final_attempt else f"wait-retry-{suffix}"
    nodes: list[dict[str, Any]] = [
        {
            "type": "send_voice",
            "id": f"voice-preop-attempt-{suffix}",
            "retell_agent_id": "",
            "voice_profile_id": VOICE_PROFILE_PLACEHOLDER,
            "wait_for_outcome": True,
            # Vendor-placement retries are deliberately separate from the three
            # patient-contact attempts represented by these distinct nodes.
            "max_attempts": 1,
            "next_node_id": f"attempt-{suffix}-confirmed",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-confirmed",
            "rules": [{"field": "call_outcome", "op": "eq", "value": "confirmed"}],
            "true_next_node_id": "write-gotracker-confirmed",
            "false_next_node_id": f"attempt-{suffix}-cancelled",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-cancelled",
            "rules": [
                {
                    "field": "call_outcome",
                    "op": "in",
                    "value": ["cancelled", "appointment_cancelled"],
                }
            ],
            "true_next_node_id": "write-gotracker-cancelled",
            "false_next_node_id": f"attempt-{suffix}-reschedule",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-reschedule",
            "rules": [
                {
                    "field": "call_outcome",
                    "op": "in",
                    "value": ["reschedule_requested", "reschedule", "appointment_requested"],
                }
            ],
            "true_next_node_id": "check-reschedule-time",
            "false_next_node_id": f"attempt-{suffix}-callback",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-callback",
            "rules": [
                {"field": "call_outcome", "op": "eq", "value": "callback_requested"}
            ],
            "true_next_node_id": callback_target,
            "false_next_node_id": f"attempt-{suffix}-dnc",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-dnc",
            "rules": [{"field": "call_outcome", "op": "eq", "value": "do_not_call"}],
            "true_next_node_id": "mark-dnc",
            "false_next_node_id": f"attempt-{suffix}-unreachable",
        },
        {
            "type": "condition",
            "id": f"attempt-{suffix}-unreachable",
            "rules": [
                {
                    "field": "call_outcome",
                    "op": "in",
                    "value": ["no_answer", "voicemail", "busy", "timeout", "declined"],
                }
            ],
            "true_next_node_id": unreachable_target,
            "false_next_node_id": "mark-followup",
        },
    ]
    if not final_attempt:
        nodes.extend(
            [
                {
                    "type": "condition",
                    "id": f"check-callback-time-{suffix}",
                    "logic": "AND",
                    "rules": [
                        {"field": "callback_at", "op": "is_not_null", "value": None},
                        {"field": "callback_at", "op": "neq", "value": ""},
                    ],
                    "true_next_node_id": f"wait-callback-{suffix}",
                    "false_next_node_id": "mark-callback-time-missing",
                },
                {
                    "type": "wait",
                    "id": f"wait-callback-{suffix}",
                    "delay": {
                        "delay_type": "appointment_relative",
                        "offset_seconds": 0,
                        "anchor_field": "callback_at",
                    },
                    "next_node_id": f"voice-preop-attempt-{attempt + 1}",
                },
                {
                    "type": "wait",
                    "id": f"wait-retry-{suffix}",
                    "delay": {"delay_type": "duration", "duration_seconds": 18000},
                    "next_node_id": f"voice-preop-attempt-{attempt + 1}",
                },
            ]
        )
    return nodes


_SURGERY_PRE_APPOINTMENT_CONFIRMATION: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {"type": "appointment_offset", "offset_hours": -24},
    "entry_node_id": "check-eligible-reason",
    "nodes": [
        {
            "type": "condition",
            "id": "check-eligible-reason",
            "logic": "AND",
            "rules": [
                {
                    "field": "appointment_status_id",
                    "op": "in_case_insensitive",
                    "value": ["1"],
                },
                {
                    "field": "appointment_reason",
                    "op": "in_case_insensitive",
                    "value": [APPOINTMENT_REASONS_PLACEHOLDER],
                }
            ],
            "true_next_node_id": "voice-preop-attempt-1",
            "false_next_node_id": "exit-ineligible-reason",
        },
        *_preappointment_attempt_nodes(1),
        *_preappointment_attempt_nodes(2),
        *_preappointment_attempt_nodes(3),
        {
            "type": "condition",
            "id": "check-reschedule-time",
            "logic": "AND",
            "rules": [
                {"field": "reschedule_start_time", "op": "is_not_null", "value": None},
                {"field": "reschedule_start_time", "op": "neq", "value": ""},
            ],
            "true_next_node_id": "write-gotracker-rescheduled",
            "false_next_node_id": "mark-reschedule-time-missing",
        },
        {
            "type": "update_gotracker_appointment",
            "id": "write-gotracker-rescheduled",
            "start_time": "{{reschedule_start_time}}",
            "next_node_id": "exit-rescheduled",
        },
        {
            "type": "update_gotracker_appointment",
            "id": "write-gotracker-confirmed",
            "confirmed": True,
            "preconfirmed": None,
            "next_node_id": "exit-confirmed",
        },
        {
            "type": "update_gotracker_appointment",
            "id": "write-gotracker-cancelled",
            "status_id": 3,
            "next_node_id": "exit-cancelled",
        },
        {
            "type": "update_patient_status",
            "id": "mark-max-attempts",
            "status": "unreachable_after_max_attempts",
            "note_template": "Pre-appointment call exhausted three attempts. Last outcome: {{call_outcome}}",
            "next_node_id": "exit-max-attempts",
        },
        {
            "type": "update_patient_status",
            "id": "mark-callback-after-max",
            "status": "callback_requested_after_max_attempts",
            "next_node_id": "exit-callback-after-max",
        },
        {
            "type": "update_patient_status",
            "id": "mark-callback-time-missing",
            "status": "callback_time_missing",
            "next_node_id": "exit-callback-time-missing",
        },
        {
            "type": "update_patient_status",
            "id": "mark-reschedule-time-missing",
            "status": "reschedule_time_missing",
            "next_node_id": "exit-reschedule-time-missing",
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
            "status": "pre_appointment_followup_needed",
            "note_template": "Pre-appointment call needs review. Outcome: {{call_outcome}}",
            "next_node_id": "exit-handoff",
        },
        {"type": "exit", "id": "exit-ineligible-reason", "outcome": "ineligible_reason"},
        {"type": "exit", "id": "exit-confirmed", "outcome": "appointment_confirmed"},
        {"type": "exit", "id": "exit-cancelled", "outcome": "appointment_cancelled"},
        {"type": "exit", "id": "exit-rescheduled", "outcome": "appointment_rescheduled"},
        {"type": "exit", "id": "exit-max-attempts", "outcome": "unreachable_after_max_attempts"},
        {"type": "exit", "id": "exit-callback-after-max", "outcome": "callback_requested_after_max_attempts"},
        {"type": "exit", "id": "exit-callback-time-missing", "outcome": "callback_time_missing"},
        {"type": "exit", "id": "exit-reschedule-time-missing", "outcome": "reschedule_time_missing"},
        {"type": "exit", "id": "exit-handoff", "outcome": "staff_handoff"},
        {"type": "exit", "id": "exit-dnc", "outcome": "do_not_call"},
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}

_POST_OP_FOLLOWUP_AFTER_CONFIRMATION: dict[str, Any] = {
    "schema_version": "1.0",
    "trigger": {
        "type": "appointment_state_changed",
        "status_ids": [],
        "confirmed": None,
        "preconfirmed": None,
        "flow_states": ["Completed"],
        "max_followup_delay_hours": 72,
        "campaign_goal": "post_op_followup",
    },
    "entry_node_id": "check-post-op-eligible-reason",
    "nodes": [
        {
            "type": "condition",
            "id": "check-post-op-eligible-reason",
            "rules": [
                {
                    "field": "appointment_reason",
                    "op": "in_case_insensitive",
                    "value": [],
                }
            ],
            "true_next_node_id": "wait-post-op",
            "false_next_node_id": "exit-ineligible-reason",
        },
        {
            "type": "wait",
            "id": "wait-post-op",
            "delay": {
                "delay_type": "appointment_relative",
                "offset_seconds": 86400,
                "anchor_field": "flow_changed_at",
            },
            "next_node_id": "voice-post-op",
        },
        {
            "type": "send_voice",
            "id": "voice-post-op",
            "retell_agent_id": "",
            "voice_profile_id": VOICE_PROFILE_PLACEHOLDER,
            "wait_for_outcome": True,
            "max_attempts": 1,
            "patient_voice_cooldown_behavior": "defer",
            "patient_voice_cooldown_deadline_field": "post_op_expires_at",
            "next_node_id": "check-post-op-cooldown-expired",
        },
        {
            "type": "condition",
            "id": "check-post-op-cooldown-expired",
            "rules": [
                {
                    "field": "call_outcome",
                    "op": "eq",
                    "value": "voice_cooldown_window_expired",
                }
            ],
            "true_next_node_id": "exit-post-op-cooldown-expired",
            "false_next_node_id": "check-post-op-dnc",
        },
        {
            "type": "condition",
            "id": "check-post-op-dnc",
            "rules": [{"field": "call_outcome", "op": "eq", "value": "do_not_call"}],
            "true_next_node_id": "mark-post-op-dnc",
            "false_next_node_id": "check-post-op-needs-review",
        },
        {
            "type": "condition",
            "id": "check-post-op-needs-review",
            "rules": [
                {
                    "field": "call_outcome",
                    "op": "neq",
                    "value": "post_op_ok",
                }
            ],
            "true_next_node_id": "mark-post-op-followup",
            "false_next_node_id": "mark-post-op-complete",
        },
        {
            "type": "update_patient_status",
            "id": "mark-post-op-complete",
            "status": "post_op_complete",
            "note_template": "Post-op call outcome: {{call_outcome}}",
            "next_node_id": "exit-post-op-complete",
        },
        {
            "type": "update_patient_status",
            "id": "mark-post-op-followup",
            "status": "post_op_followup_needed",
            "note_template": "Post-op call needs staff review. Outcome: {{call_outcome}}",
            "next_node_id": "exit-post-op-followup",
        },
        {
            "type": "update_patient_status",
            "id": "mark-post-op-dnc",
            "status": "do_not_call_requested",
            "note_template": "Patient requested no further calls during post-op outreach.",
            "next_node_id": "exit-post-op-dnc",
        },
        {"type": "exit", "id": "exit-post-op-complete", "outcome": "post_op_complete"},
        {"type": "exit", "id": "exit-post-op-followup", "outcome": "staff_handoff"},
        {"type": "exit", "id": "exit-post-op-dnc", "outcome": "do_not_call"},
        {"type": "exit", "id": "exit-ineligible-reason", "outcome": "ineligible_reason"},
        {
            "type": "exit",
            "id": "exit-post-op-cooldown-expired",
            "outcome": "post_op_cooldown_expired",
        },
    ],
    "compliance": {"content_class": "transactional_care", "consent_required": True},
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ALL_TEMPLATES: dict[str, CampaignTemplate] = {
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
    "surgery-pre-appointment-confirmation": CampaignTemplate(
        id="surgery-pre-appointment-confirmation",
        name="Surgery Pre-Appointment Confirmation",
        description=(
            "Call patients before major appointments to confirm whether they "
            "still plan to attend."
        ),
        trigger_type="appointment_offset",
        definition=_SURGERY_PRE_APPOINTMENT_CONFIRMATION,
        metadata=_metadata(
            category="appointment_ops",
            goal="Confirm major appointments before the visit and write confirmed or cancelled outcomes back to GoTracker.",
            outcome_labels=[
                "appointment_confirmed",
                "appointment_cancelled",
                "appointment_rescheduled",
                "unreachable_after_max_attempts",
                "callback_requested_after_max_attempts",
                "callback_time_missing",
                "reschedule_time_missing",
                "staff_handoff",
                "do_not_call",
            ],
            supported_channels=["voice"],
            required_readiness_checks=["location", "nexhealth_appointment_data", "voice", "consent", "quiet_hours"],
            required_merge_fields=[
                "patient_first_name",
                "clinic_name",
                "appointment_date",
                "appointment_time",
                "appointment_reason",
            ],
            content_class="transactional_care",
            audience="Appointments whose GoTracker reason is routed by workflow nodes",
            eligibility=[
                "appointment reason matches the workflow's mapper/condition logic",
                "future appointment still exists",
                "patient is not suppressed",
                "voice consent exists",
            ],
            handoff_reason="reschedule_or_followup_needed",
            analytics={
                "appointment_confirmed": "confirmed",
                "appointment_cancelled": "cancelled",
                "appointment_rescheduled": "reschedule",
                "unreachable_after_max_attempts": "unreachable",
                "callback_requested_after_max_attempts": "handoff",
                "callback_time_missing": "handoff",
                "reschedule_time_missing": "handoff",
                "staff_handoff": "handoff",
                "do_not_call": "opt_out",
            },
            sample_context={
                "patient_first_name": "Jordan",
                "clinic_name": "Riverside Dental",
                "appointment_date": "July 22, 2026",
                "appointment_time": "2:00 PM",
                "appointment_reason": "implant surgery",
                "call_outcome": "confirmed",
            },
            setup_fields=[
                {
                    "id": "voice_profile_id",
                    "label": "Surgery confirmation voice profile",
                    "type": "voice_profile_select",
                    "required": True,
                    "placeholder": "Choose outbound voice profile",
                },
                {
                    "id": "appointment_reasons",
                    "label": "Eligible GoTracker reasons",
                    "type": "string_list",
                    "required": True,
                    "placeholder": "bridge prep, implant surgery",
                },
                {
                    "id": "call_offset_hours_before",
                    "label": "Initial call hours before appointment",
                    "type": "number",
                    "required": True,
                    "default": 24,
                },
                {
                    "id": "retry_delay_1_hours",
                    "label": "Delay before second attempt (hours)",
                    "type": "number",
                    "required": True,
                    "default": 5,
                },
                {
                    "id": "retry_delay_2_hours",
                    "label": "Delay before third attempt (hours)",
                    "type": "number",
                    "required": True,
                    "default": 5,
                },
                {
                    "id": "patient_voice_cooldown_hours",
                    "label": "Patient voice cooldown (hours)",
                    "type": "number",
                    "required": True,
                    "default": 24,
                },
            ],
            frequency_cap=TemplateFrequencyCap(
                max_per_day=3,
                max_per_rolling_7_days=3,
            ),
        ),
        tags=["appointment", "surgery", "voice", "confirmation"],
    ),
    "post-op-followup-after-confirmation": CampaignTemplate(
        id="post-op-followup-after-confirmation",
        name="Post-Op Follow-Up After Completed Visit",
        description=(
            "Call patients after a completed surgical/major appointment "
            "to check whether staff follow-up is needed."
        ),
            trigger_type="appointment_state_changed",
            definition=_POST_OP_FOLLOWUP_AFTER_CONFIRMATION,
            metadata=_metadata(
                category="appointment_ops",
                goal="Complete configurable post-op follow-up after Tracker marks an eligible appointment Completed.",
            outcome_labels=["post_op_complete", "staff_handoff", "do_not_call"],
            supported_channels=["voice"],
            required_readiness_checks=["location", "voice", "consent", "quiet_hours"],
            required_merge_fields=["patient_first_name", "clinic_name", "appointment_date", "appointment_time"],
            content_class="transactional_care",
                audience="Eligible appointments whose Tracker Chair Flow state became Completed",
                eligibility=[
                    "Tracker Chair Flow state is Completed",
                    "appointment reason is selected during setup",
                    "source appointment includes FlowChange",
                    "voice consent exists",
                    "patient is not suppressed",
                ],
            handoff_reason="post_op_followup_needed",
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
                "call_outcome": "post_op_ok",
                "appointment_flow_state": "Completed",
                "flow_changed_at": "2026-07-22T14:00:00+00:00",
                "gotracker_status_id": 1,
            },
            setup_fields=[
                {
                    "id": "voice_profile_id",
                    "label": "Post-op voice profile",
                    "type": "voice_profile_select",
                    "required": True,
                    "placeholder": "Choose outbound voice profile",
                },
                {
                    "id": "post_op_reasons",
                    "label": "Eligible completed GoTracker reasons",
                    "type": "string_list",
                    "required": True,
                    "placeholder": "implant surgery, extraction",
                },
                {
                    "id": "post_op_delay_hours",
                    "label": "Hours after completion before calling",
                    "type": "number",
                    "required": True,
                    "default": 24,
                },
                {
                    "id": "post_op_latest_call_hours",
                    "label": "Latest allowed post-op call (hours after completion)",
                    "type": "number",
                    "required": True,
                    "default": 72,
                },
                {
                    "id": "patient_voice_cooldown_hours",
                    "label": "Patient voice cooldown (hours)",
                    "type": "number",
                    "required": True,
                    "default": 24,
                },
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
                    "id": "voice_profile_id",
                    "label": "Voice profile",
                    "type": "voice_profile_select",
                    "required": True,
                    "placeholder": "Choose outbound voice profile",
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

LAUNCH_TEMPLATE_IDS: tuple[str, ...] = (
    "surgery-pre-appointment-confirmation",
    "post-op-followup-after-confirmation",
)

# Client-facing launch scope: only expose the two workflows requested for the
# current production rollout. Keep the other template definitions above so they
# can be re-enabled later without rebuilding them.
TEMPLATES: dict[str, CampaignTemplate] = {
    template_id: _ALL_TEMPLATES[template_id] for template_id in LAUNCH_TEMPLATE_IDS
}


def get_template(template_id: str) -> CampaignTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[CampaignTemplate]:
    return list(TEMPLATES.values())
