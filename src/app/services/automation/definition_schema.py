"""Pydantic schema for workflow definition JSON stored in AutomationWorkflowVersion.definition.

Definitions are immutable once published. Schema version "1.0" supports:
  Triggers: appointment_offset, recall_scan, manual, bulk_import
  Nodes:    wait, send_sms, send_voice, send_email, condition, exit
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


WorkflowTrigger = Annotated[
    Union[
        AppointmentOffsetTrigger,
        RecallScanTrigger,
        ManualTrigger,
        BulkImportTrigger,
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


WaitDelay = Annotated[
    Union[DurationDelay, CalendarDelay],
    Field(discriminator="delay_type"),
]

# ---------------------------------------------------------------------------
# Condition rule
# ---------------------------------------------------------------------------

_RULE_VALUE = bool | int | str | list[str] | None


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    op: Literal["eq", "neq", "in", "not_in", "is_null", "is_not_null"]
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
    retell_agent_id: str = Field(min_length=1)
    next_node_id: str
    respect_quiet_hours: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)


class SendEmailNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["send_email"] = "send_email"
    subject_template: str = Field(min_length=1)
    body_template: str = Field(min_length=1)
    next_node_id: str
    respect_quiet_hours: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)


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
    Union[WaitNode, SendSmsNode, SendVoiceNode, SendEmailNode, ConditionNode, ExitNode],
    Field(discriminator="type"),
]

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

    @model_validator(mode="after")
    def validate_graph_structure(self) -> "WorkflowDefinition":
        node_ids = {n.id for n in self.nodes}

        if self.entry_node_id not in node_ids:
            raise ValueError(
                f"entry_node_id '{self.entry_node_id}' not found in nodes"
            )

        for node in self.nodes:
            if isinstance(node, (WaitNode, SendSmsNode, SendVoiceNode, SendEmailNode)):
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
