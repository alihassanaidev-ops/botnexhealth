"""Pydantic schema for workflow definition JSON stored in AutomationWorkflowVersion.definition.

Definitions are immutable once published. Schema version "1.0" supports:
  Triggers: appointment_offset, appointment_state_changed, recall_scan, manual,
            bulk_import, callback_requested, patient_status_changed, sms_reply
  Nodes:    wait, drip, send_sms, send_voice, send_email,
            update_patient_status, update_gotracker_appointment, json_mapper,
            llm, condition, exit
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PHONE_COUNTRY_REGIONS = frozenset(phonenumbers.SUPPORTED_REGIONS)

# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class AppointmentOffsetTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["appointment_offset"] = "appointment_offset"
    offset_hours: int
    # Legacy authoring field. Kept for backward compatibility with published
    # definitions, but appointment-type filtering no longer happens at trigger
    # selection time.
    appointment_type_ids: list[str] | None = None


class AppointmentStateChangedTrigger(BaseModel):
    """Enroll when cached GoTracker appointment state matches configured values."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["appointment_state_changed"] = "appointment_state_changed"
    status_ids: list[int] = Field(default_factory=list)
    confirmed: bool | None = None
    preconfirmed: bool | None = None
    flow_states: list[str] = Field(default_factory=list)
    # A campaign-specific deadline measured from FlowChange. It is used by the
    # post-op template when a voice cooldown defers the call.
    max_followup_delay_hours: int | None = Field(default=None, ge=0, le=168)
    campaign_goal: str | None = None

    @field_validator("status_ids")
    @classmethod
    def validate_status_ids(cls, values: list[int]) -> list[int]:
        unique: list[int] = []
        for value in values:
            if value < 1 or value > 9:
                raise ValueError("status_ids must be between 1 and 9")
            if value not in unique:
                unique.append(value)
        return unique

    @field_validator("flow_states")
    @classmethod
    def validate_flow_states(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned.casefold() not in {
                item.casefold() for item in normalized
            }:
                normalized.append(cleaned)
        return normalized

    @model_validator(mode="after")
    def require_matcher(self) -> "AppointmentStateChangedTrigger":
        if (
            not self.status_ids
            and self.confirmed is None
            and self.preconfirmed is None
            and not self.flow_states
        ):
            raise ValueError("appointment_state_changed needs at least one matcher")
        return self

    @field_validator("campaign_goal")
    @classmethod
    def normalize_campaign_goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RecallScanTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["recall_scan"] = "recall_scan"
    recall_interval_months: int = Field(ge=1)


class ManualTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["manual"] = "manual"


class BulkImportTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bulk_import"] = "bulk_import"


class CallbackRequestedTrigger(BaseModel):
    """Enroll when an inbound call is classified 'needs_callback' (Plan 07).

    A clinic opts into AI-handled callbacks by activating a workflow with this
    trigger; with none active, callbacks stay in the manual queue (default).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["callback_requested"] = "callback_requested"


class PatientStatusChangedTrigger(BaseModel):
    """Enroll when a workflow records a matching local patient status event."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["patient_status_changed"] = "patient_status_changed"
    statuses: list[str] = Field(min_length=1)
    campaign_goal: str | None = None

    @field_validator("statuses")
    @classmethod
    def validate_statuses(cls, values: list[str]) -> list[str]:
        statuses = [status.strip() for status in values if status.strip()]
        if not statuses:
            raise ValueError("statuses must include at least one non-empty value")
        return statuses

    @field_validator("campaign_goal")
    @classmethod
    def normalize_campaign_goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SmsReplyTrigger(BaseModel):
    """Enroll when an inbound patient SMS matches optional whole-token filters."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sms_reply"] = "sms_reply"
    tokens: list[str] = Field(default_factory=list)
    campaign_goal: str | None = None

    @field_validator("tokens")
    @classmethod
    def normalize_tokens(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            token = value.strip()
            if token and token.casefold() not in {item.casefold() for item in normalized}:
                normalized.append(token)
        return normalized

    @field_validator("campaign_goal")
    @classmethod
    def normalize_campaign_goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


WorkflowTrigger = Annotated[
    Union[
        AppointmentOffsetTrigger,
        AppointmentStateChangedTrigger,
        RecallScanTrigger,
        ManualTrigger,
        BulkImportTrigger,
        CallbackRequestedTrigger,
        PatientStatusChangedTrigger,
        SmsReplyTrigger,
    ],
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# Wait delay configs (discriminated by delay_type)
# ---------------------------------------------------------------------------


class DurationDelay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_type: Literal["duration"] = "duration"
    duration_seconds: int = Field(ge=0)


class CalendarDelay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_type: Literal["calendar"] = "calendar"
    offset_days: int
    time_of_day: str = Field(pattern=r"^\d{2}:\d{2}$", description="HH:MM in location timezone")


class AppointmentRelativeDelay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_type: Literal["appointment_relative"] = "appointment_relative"
    offset_seconds: int
    anchor_field: str = "appointment_at"


WaitDelay = Annotated[
    Union[DurationDelay, CalendarDelay, AppointmentRelativeDelay],
    Field(discriminator="delay_type"),
]

# ---------------------------------------------------------------------------
# Condition rule
# ---------------------------------------------------------------------------

_RULE_VALUE = bool | int | str | list[str] | None


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    op: Literal[
        "eq",
        "neq",
        "in",
        "in_case_insensitive",
        "not_in",
        "is_null",
        "is_not_null",
        "contains",
        "not_contains",
    ]
    value: _RULE_VALUE = None

    @field_validator("value", mode="before")
    @classmethod
    def list_items_must_be_strings(cls, v: object) -> object:
        if isinstance(v, list) and not all(isinstance(i, str) for i in v):
            raise ValueError("list values must contain only strings")
        return v


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


class SmsResponseMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: list[str] = Field(default_factory=list)
    context_updates: dict[str, Any] = Field(default_factory=dict)
    handoff_reason: str | None = None

    @field_validator("tokens")
    @classmethod
    def normalize_tokens(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            token = value.strip()
            if token and token.casefold() not in {item.casefold() for item in normalized}:
                normalized.append(token)
        return normalized


class TimeWaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["time"] = "time"
    delay: WaitDelay
    respect_quiet_hours: bool = True


class SmsReplyWaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sms_reply"] = "sms_reply"
    response_window_seconds: int = Field(default=259200, ge=60, le=2592000)
    response_mappings: list[SmsResponseMapping] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_deprecated_reply_key(cls, value: object) -> object:
        """Keep published definitions loadable after reply-key removal."""
        if not isinstance(value, dict) or "include_reply_key" not in value:
            return value
        cleaned = dict(value)
        cleaned.pop("include_reply_key", None)
        return cleaned


WaitForConfig = Annotated[
    Union[TimeWaitConfig, SmsReplyWaitConfig],
    Field(discriminator="type"),
]


class WaitNode(BaseModel):
    """One public wait node with typed time/event behavior.

    The before-validator upgrades the original ``wait`` shape so already-published
    definitions continue to execute without a data migration.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["wait"] = "wait"
    wait_for: WaitForConfig
    next_node_id: str

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_time_wait(cls, value: object) -> object:
        if not isinstance(value, dict) or "wait_for" in value or "delay" not in value:
            return value
        upgraded = dict(value)
        upgraded["wait_for"] = {
            "type": "time",
            "delay": upgraded.pop("delay"),
            "respect_quiet_hours": upgraded.pop("respect_quiet_hours", True),
        }
        return upgraded


class WaitForSmsReplyNode(BaseModel):
    """Legacy compatibility input. New definitions use WaitNode + SmsReplyWaitConfig."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["wait_for_sms_reply"] = "wait_for_sms_reply"
    next_node_id: str
    response_window_seconds: int = Field(default=259200, ge=60, le=2592000)
    response_mappings: list[SmsResponseMapping] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_deprecated_reply_key(cls, value: object) -> object:
        """Keep legacy published definitions loadable after reply-key removal."""
        if not isinstance(value, dict) or "include_reply_key" not in value:
            return value
        cleaned = dict(value)
        cleaned.pop("include_reply_key", None)
        return cleaned


class SmsReplyWaitSpec(BaseModel):
    """Small internal interface shared by SMS correlation and dispatch modules."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    response_window_seconds: int
    response_mappings: list[SmsResponseMapping]


def sms_reply_wait_spec(
    node: WaitNode | WaitForSmsReplyNode | object,
) -> SmsReplyWaitSpec | None:
    if isinstance(node, WaitNode) and isinstance(node.wait_for, SmsReplyWaitConfig):
        config = node.wait_for
    elif isinstance(node, WaitForSmsReplyNode):
        config = node
    else:
        return None
    return SmsReplyWaitSpec(
        node_id=node.id,
        response_window_seconds=config.response_window_seconds,
        response_mappings=config.response_mappings,
    )


class DripNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["drip"] = "drip"
    batch_size: int = Field(ge=1, le=10_000)
    interval_seconds: int = Field(ge=1)
    next_node_id: str


class SendSmsNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["send_sms"] = "send_sms"
    body_template: str = Field(min_length=1)
    next_node_id: str
    respect_quiet_hours: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)
    expect_response: bool = False
    response_window_seconds: int = Field(default=259200, ge=60, le=2592000)
    response_mappings: list[SmsResponseMapping] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_deprecated_reply_key(cls, value: object) -> object:
        """Keep published definitions loadable after reply-key removal."""
        if not isinstance(value, dict) or "include_reply_key" not in value:
            return value
        cleaned = dict(value)
        cleaned.pop("include_reply_key", None)
        return cleaned


class SendVoiceNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["send_voice"] = "send_voice"
    # Legacy fallback. New workflows should prefer voice_profile_id so changing a
    # Retell agent later does not require editing every workflow definition.
    retell_agent_id: str = ""
    voice_profile_id: str | None = None
    next_node_id: str
    respect_quiet_hours: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)
    # Patient-level safety guard across workflow runs. Retries inside the same
    # run are allowed; this prevents a second appointment/campaign run from
    # dialing the same patient too soon. 0 disables the guard.
    patient_voice_cooldown_hours: int = Field(default=24, ge=0, le=168)
    # Most campaigns skip a cross-run cooldown conflict. Care workflows may
    # defer until it expires instead, optionally bounded by a context deadline.
    patient_voice_cooldown_behavior: Literal["skip", "defer"] = "skip"
    patient_voice_cooldown_deadline_field: str | None = None
    # Optional workflow-level override for local-format patient phone numbers.
    # When disabled, only already-international numbers are accepted for voice.
    phone_country_code_enabled: bool = False
    phone_country_region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    # When true, after the call is placed the run PARKS (WAITING) until the Retell
    # post-call webhook resumes it with the dial outcome (written to run context as
    # `call_outcome` for a following ConditionNode to branch on). When false the node
    # is fire-and-forget (advances immediately). Plan 03 outcome-feedback loop.
    wait_for_outcome: bool = False

    @field_validator("phone_country_region", mode="before")
    @classmethod
    def normalize_phone_country_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        region = str(value).strip().upper()
        return region or None

    @field_validator("phone_country_region")
    @classmethod
    def validate_phone_country_region(cls, value: str | None) -> str | None:
        if value is not None and value not in PHONE_COUNTRY_REGIONS:
            raise ValueError("phone_country_region must be a supported ISO country region")
        return value

    @model_validator(mode="after")
    def require_phone_country_when_enabled(self) -> "SendVoiceNode":
        if self.phone_country_code_enabled and not self.phone_country_region:
            raise ValueError("phone_country_region is required when phone country code is enabled")
        return self


class SendEmailNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["send_email"] = "send_email"
    subject_template: str = Field(min_length=1)
    body_template: str = Field(min_length=1)
    next_node_id: str
    respect_quiet_hours: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)


class UpdatePatientStatusNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["update_patient_status"] = "update_patient_status"
    status: str = Field(min_length=1, max_length=80)
    next_node_id: str
    note_template: str | None = None


class UpdateGoTrackerAppointmentNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["update_gotracker_appointment"] = "update_gotracker_appointment"
    next_node_id: str
    status_id: int | None = Field(default=None, ge=1, le=9)
    confirmed: bool | None = None
    preconfirmed: bool | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_min: int | None = Field(default=None, ge=1)
    provider_id: str | None = None
    operatory_id: str | None = None
    patient_id: str | None = None
    reason: str | None = None

    @field_validator(
        "start_time",
        "end_time",
        "provider_id",
        "operatory_id",
        "patient_id",
        "reason",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_update(self) -> "UpdateGoTrackerAppointmentNode":
        if (
            self.status_id is None
            and self.confirmed is None
            and self.preconfirmed is None
            and self.start_time is None
            and self.end_time is None
            and self.duration_min is None
            and self.provider_id is None
            and self.operatory_id is None
            and self.patient_id is None
            and self.reason is None
        ):
            raise ValueError("at least one GoTracker appointment update field is required")
        return self


class JsonMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)
    target_field: str = Field(min_length=1)
    default_value: _RULE_VALUE = None


class JsonMapperNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["json_mapper"] = "json_mapper"
    mappings: list[JsonMapping] = Field(min_length=1)
    next_node_id: str


class LlmLabelRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        keywords = [keyword.strip() for keyword in values if keyword.strip()]
        if not keywords:
            raise ValueError("keywords must include at least one non-empty value")
        return keywords


class LlmNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["llm"] = "llm"
    source_field: str = Field(min_length=1)
    output_field: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    model: str | None = None
    output_mode: Literal["label", "text", "json"] = "label"
    max_output_tokens: int = Field(default=256, ge=1, le=4096)
    include_context: bool = False
    require_model: bool = True
    allow_keyword_fallback: bool | None = None
    json_schema: dict[str, Any] | None = None
    labels: list[str] = Field(default_factory=list)
    label_rules: list[LlmLabelRule] = Field(default_factory=list)
    fallback_label: str | None = "unknown"
    next_node_id: str

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, values: list[str]) -> list[str]:
        return [label.strip() for label in values if label.strip()]

    @field_validator("fallback_label")
    @classmethod
    def normalize_fallback_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConditionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["condition"] = "condition"
    logic: Literal["AND", "OR"] = "AND"
    rules: list[ConditionRule] = Field(min_length=1)
    true_next_node_id: str
    false_next_node_id: str


class ExitNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["exit"] = "exit"
    outcome: str | None = None


WorkflowNode = Annotated[
    Union[
        WaitNode,
        WaitForSmsReplyNode,
        DripNode,
        SendSmsNode,
        SendVoiceNode,
        SendEmailNode,
        UpdatePatientStatusNode,
        UpdateGoTrackerAppointmentNode,
        JsonMapperNode,
        LlmNode,
        ConditionNode,
        ExitNode,
    ],
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# Compliance metadata + visual layout (non-executable)
# ---------------------------------------------------------------------------


class ComplianceMetadata(BaseModel):
    """Compliance classification for the workflow. Consumed by the validation
    service (consent-path + content-class checks) and rendered in the builder's
    validation panel. The semantic content/PHI/blast-radius validators are owned
    by Plan 12; this block carries the classification they act on."""

    model_config = ConfigDict(extra="forbid")

    # exempt-care/recall vs. marketing drives the consent basis and content rules.
    content_class: Literal["transactional_care", "recall", "sales", "marketing"] | None = None
    # Whether send steps require a recorded consent record on the channel.
    consent_required: bool = True


class NodeLayout(BaseModel):
    """Visual canvas coordinates for a node. Purely presentational — never read by
    the runtime (execution semantics come from node ids/edges, not coordinates)."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


# ---------------------------------------------------------------------------
# Top-level definition
# ---------------------------------------------------------------------------


class WorkflowDefinition(BaseModel):
    """Immutable workflow definition stored in AutomationWorkflowVersion.definition."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    trigger: WorkflowTrigger
    entry_node_id: str
    nodes: list[WorkflowNode] = Field(min_length=1)
    compliance: ComplianceMetadata | None = None
    # node_id -> {x, y}; presentational only, ignored by the runtime.
    layout: dict[str, NodeLayout] | None = None

    @model_validator(mode="after")
    def validate_graph_structure(self) -> "WorkflowDefinition":
        node_ids = {n.id for n in self.nodes}

        if self.entry_node_id not in node_ids:
            raise ValueError(
                f"entry_node_id '{self.entry_node_id}' not found in nodes"
            )

        for node in self.nodes:
            if isinstance(
                node,
                (
                    WaitNode,
                    WaitForSmsReplyNode,
                    DripNode,
                    SendSmsNode,
                    SendVoiceNode,
                    SendEmailNode,
                    UpdatePatientStatusNode,
                    UpdateGoTrackerAppointmentNode,
                    JsonMapperNode,
                    LlmNode,
                ),
            ):
                if node.next_node_id not in node_ids:
                    raise ValueError(
                        f"node '{node.id}' next_node_id '{node.next_node_id}' not found in nodes"
                    )
            elif isinstance(node, ConditionNode):
                for ref_name, ref_id in (
                    ("true_next_node_id", node.true_next_node_id),
                    ("false_next_node_id", node.false_next_node_id),
                ):
                    if ref_id not in node_ids:
                        raise ValueError(
                            f"condition node '{node.id}' {ref_name} '{ref_id}' not found in nodes"
                        )

        if not any(isinstance(n, ExitNode) for n in self.nodes):
            raise ValueError("workflow definition must contain at least one exit node")

        return self
