"""Pydantic schema for workflow definition JSON stored in AutomationWorkflowVersion.definition.

Definitions are immutable once published. Schema version "1.0" supports:
  Triggers: appointment_offset, recall_scan, manual, bulk_import, callback_requested,
            patient_status_changed
  Nodes:    wait, drip, send_sms, send_voice, send_email, update_patient_status,
            condition, exit
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

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
    appointment_type_ids: list[str] | None = None


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


WorkflowTrigger = Annotated[
    Union[
        AppointmentOffsetTrigger,
        RecallScanTrigger,
        ManualTrigger,
        BulkImportTrigger,
        CallbackRequestedTrigger,
        PatientStatusChangedTrigger,
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


class WaitNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["wait"] = "wait"
    delay: WaitDelay
    next_node_id: str
    respect_quiet_hours: bool = True


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
        DripNode,
        SendSmsNode,
        SendVoiceNode,
        SendEmailNode,
        UpdatePatientStatusNode,
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
                    DripNode,
                    SendSmsNode,
                    SendVoiceNode,
                    SendEmailNode,
                    UpdatePatientStatusNode,
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
