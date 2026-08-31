"""Pydantic schema for workflow definition JSON stored in AutomationWorkflowVersion.definition.

Definitions are immutable once published. Schema version "1.0" supports:
  Triggers: appointment_offset, appointment_state_changed, recall_scan, manual,
            bulk_import, callback_requested, patient_status_changed, sms_reply
  Nodes:    wait, drip, send_sms, retell_sms_conversation, send_voice, send_email,
            update_patient_status, update_appointment,
            update_gotracker_appointment, json_mapper, llm, condition, exit

``update_appointment`` is the PMS-neutral appointment write-back and should be
preferred; ``update_gotracker_appointment`` only runs on GoTracker locations and
is retained for already-published definitions.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.app.services.automation.node_registry import outgoing_references

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


class EmailReplyTrigger(BaseModel):
    """Enroll when an inbound patient email matches optional whole-token filters.

    The email counterpart to ``SmsReplyTrigger``. Only replies that routed to a
    known clinic reach this — unattributable mail is held, never enrolled.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["email_reply"] = "email_reply"
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
        EmailReplyTrigger,
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


class EmailReplyWaitConfig(BaseModel):
    """Park the run until the patient replies to the email, or the window closes.

    The default window is a week rather than SMS's three days: people answer
    email on a slower rhythm, and a campaign that gives up after 72 hours would
    treat an ordinary weekend as a non-response.

    ``response_mappings`` reuses ``SmsResponseMapping`` — it is a token-to-context
    mapping, not anything SMS-specific — so a workflow author configures replies
    the same way on both channels.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["email_reply"] = "email_reply"
    response_window_seconds: int = Field(default=604800, ge=60, le=2592000)
    response_mappings: list[SmsResponseMapping] = Field(default_factory=list)


WaitForConfig = Annotated[
    Union[TimeWaitConfig, SmsReplyWaitConfig, EmailReplyWaitConfig],
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


class EmailReplyWaitSpec(BaseModel):
    """Internal interface shared by email correlation and dispatch."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    response_window_seconds: int
    response_mappings: list[SmsResponseMapping]


def email_reply_wait_spec(node: object) -> EmailReplyWaitSpec | None:
    if not isinstance(node, WaitNode) or not isinstance(node.wait_for, EmailReplyWaitConfig):
        return None
    return EmailReplyWaitSpec(
        node_id=node.id,
        response_window_seconds=node.wait_for.response_window_seconds,
        response_mappings=node.wait_for.response_mappings,
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
    include_opt_out_footer: bool = True
    respect_quiet_hours: bool = True
    # Once a patient responds the engine stops the rest of this run's sends on
    # every channel, whether or not the campaign drew a branch for it. Set this
    # on a step whose send after a response is deliberate — an acknowledgement,
    # say — rather than part of an attempt ladder.
    send_after_response: bool = False
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


_LEGACY_RETELL_SMS_POLICY_FIELDS = frozenset(
    {
        "inactivity_timeout_seconds",
        "max_duration_seconds",
        "max_patient_turns",
        "dynamic_variable_mappings",
        "human_handoff_tokens",
        "timeout_behavior",
        "failure_behavior",
        "respect_quiet_hours",
        "max_response_segments",
    }
)


class RetellSmsConversationNode(BaseModel):
    """Park a run while Retell generates replies for a Twilio SMS conversation.

    BotNexHealth remains the transport and lifecycle owner. A Retell ``chat_id``
    is created lazily on the first patient reply and is never used as the local
    conversation identity.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["retell_sms_conversation"] = "retell_sms_conversation"
    chat_profile_id: str = Field(min_length=1)
    next_node_id: str
    @model_validator(mode="before")
    @classmethod
    def discard_legacy_author_policy(cls, value: Any) -> Any:
        """Load old published definitions while enforcing platform policy now."""
        if not isinstance(value, dict):
            return value
        return {
            key: item
            for key, item in value.items()
            if key not in _LEGACY_RETELL_SMS_POLICY_FIELDS
        }


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
    # Once a patient responds the engine stops the rest of this run's sends on
    # every channel, whether or not the campaign drew a branch for it. Set this
    # on a step whose send after a response is deliberate — an acknowledgement,
    # say — rather than part of an attempt ladder.
    send_after_response: bool = False
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


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class ContactRecipient(BaseModel):
    """Send to the enrolled patient. The behaviour of every definition
    published before ``recipient`` existed."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["contact"] = "contact"


class StaffRecipient(BaseModel):
    """Send to the clinic's own staff — institution admins plus the run
    location's admins and staff. Used for internal alerts."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["staff"] = "staff"
    # When set, users who opted out of this notification type are excluded and
    # matching external recipients are included.
    notification_type: str | None = Field(default=None, max_length=50)
    include_external: bool = True


class StaticRecipient(BaseModel):
    """Send to fixed addresses — an ops mailbox, a monitoring alias."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["static"] = "static"
    addresses: list[str] = Field(min_length=1, max_length=10)

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            address = (raw or "").strip()
            if not _EMAIL_RE.match(address):
                raise ValueError(f"'{raw}' is not a valid email address")
            cleaned.append(address)
        return cleaned


class MergeFieldRecipient(BaseModel):
    """Send to an address resolved from a merge field at send time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["merge_field"] = "merge_field"
    field: str = Field(min_length=1, max_length=80)


EmailRecipient = Annotated[
    ContactRecipient | StaffRecipient | StaticRecipient | MergeFieldRecipient,
    Field(discriminator="kind"),
]


class SendEmailNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["send_email"] = "send_email"
    # Inline content. Required unless ``template_key`` names a saved template.
    subject_template: str = Field(default="", max_length=500)
    body_template: str = Field(default="")
    # Optional HTML part sent alongside the text one. Inline mode only; a saved
    # template carries its own HTML.
    html_template: str | None = None
    # Reference to a CampaignEmailTemplate owned by this institution. When set,
    # the saved template supplies subject, text and HTML, so editing it once
    # updates every campaign that uses it.
    template_key: str | None = Field(default=None, max_length=80)
    next_node_id: str
    respect_quiet_hours: bool = True
    # Once a patient responds the engine stops the rest of this run's sends on
    # every channel, whether or not the campaign drew a branch for it. Set this
    # on a step whose send after a response is deliberate — an acknowledgement,
    # say — rather than part of an attempt ladder.
    send_after_response: bool = False
    max_attempts: int = Field(default=1, ge=1, le=3)
    # Who receives this email. Defaults to the enrolled patient so definitions
    # published before this field existed keep their original behaviour.
    recipient: EmailRecipient = Field(default_factory=ContactRecipient)
    # A courtesy email failing should not necessarily kill the whole run.
    # Defaults to the historical behaviour (fail the run).
    on_failure: Literal["fail_run", "continue"] = "fail_run"

    @model_validator(mode="after")
    def require_content(self) -> "SendEmailNode":
        """Exactly one content source: a saved template, or inline text.

        Inline subject/body stayed required-by-default so every definition
        published before ``template_key`` existed still validates unchanged.
        """
        if self.template_key:
            if self.subject_template or self.body_template or self.html_template:
                raise ValueError(
                    "send_email: use either template_key or inline content, not both"
                )
            return self
        if not self.subject_template.strip():
            raise ValueError("send_email: subject_template is required")
        if not self.body_template.strip():
            raise ValueError("send_email: body_template is required")
        return self

    @property
    def is_patient_directed(self) -> bool:
        """True when this email goes to the patient.

        ``merge_field`` counts as patient-directed: it usually resolves to the
        contact, and where it does not, keeping the consent check is the
        conservative outcome — a send that is wrongly blocked is recoverable,
        one that wrongly bypasses consent is not.
        """
        return self.recipient.kind in ("contact", "merge_field")


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


class UpdateAppointmentNode(BaseModel):
    """PMS-neutral appointment write-back.

    Routes through the ``PMSAdapter`` contract so one campaign definition writes
    back on any PMS. Prefer this over :class:`UpdateGoTrackerAppointmentNode`,
    which only runs on GoTracker locations and is kept for already-published
    definitions.

    ``reschedule`` requires ``start_time``. Note that reschedule semantics differ
    by PMS — GoTracker updates in place, NexHealth books the new slot and cancels
    the old one, which yields a new appointment id.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["update_appointment"] = "update_appointment"
    next_node_id: str
    operation: Literal["confirm", "cancel", "reschedule"]
    start_time: str | None = None
    end_time: str | None = None
    duration_min: int | None = Field(default=None, ge=1)
    provider_id: str | None = None
    operatory_id: str | None = None
    reason: str | None = None

    @field_validator(
        "start_time",
        "end_time",
        "provider_id",
        "operatory_id",
        "reason",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_reschedule_target(self) -> "UpdateAppointmentNode":
        if self.operation == "reschedule" and self.start_time is None:
            raise ValueError("update_appointment reschedule requires start_time")
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
        RetellSmsConversationNode,
        SendVoiceNode,
        SendEmailNode,
        UpdatePatientStatusNode,
        UpdateGoTrackerAppointmentNode,
        UpdateAppointmentNode,
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
    # What this campaign needs the clinic's practice software to provide. Carried
    # from the template it was built from so publishing can re-check it: a clinic
    # can change practice software, and a workflow not built from a template
    # would otherwise never be checked at all.
    pms_capability_requirements: list[str] = Field(default_factory=list)
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
            for ref_name, ref_id in outgoing_references(node):
                if ref_id not in node_ids:
                    raise ValueError(
                        f"node '{node.id}' {ref_name} '{ref_id}' not found in nodes"
                    )

        if not any(isinstance(n, ExitNode) for n in self.nodes):
            raise ValueError("workflow definition must contain at least one exit node")

        return self
