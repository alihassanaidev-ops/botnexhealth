"""Campaign launch-readiness checklist (Plan 02).

This composes existing validation/readiness services into one product-facing
object. It is intentionally read-only: publish validation remains authoritative,
while this report explains the launch state and dependency gaps in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.automation_workflow import AutomationWorkflow
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.channel_readiness import ChannelReadinessService
from src.app.services.automation.content_compliance_validator import ContentComplianceValidator
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.validation_service import WorkflowValidationService

ChecklistStatus = Literal["pass", "warning", "blocked", "unknown"]

_SEND_NODE_TYPES = (SendSmsNode, SendEmailNode, SendVoiceNode)
_BROAD_TRIGGER_TYPES = {"recall_scan"}
_APPOINTMENT_TRIGGER_TYPES = {"appointment_offset", "recall_scan"}
_FRESHNESS_WINDOW = timedelta(hours=24)
_SMS_STOP_HELP_COPY = "SMS bodies are normalized with clinic identity plus STOP/HELP copy at send time."


@dataclass(frozen=True)
class CampaignLaunchChecklistItem:
    id: str
    section: str
    label: str
    status: ChecklistStatus
    message: str
    fix_href: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignLaunchChecklist:
    workflow_id: str
    workflow_version_id: str | None
    location_id: str | None
    overall_status: ChecklistStatus
    blockers_count: int
    warnings_count: int
    unknown_count: int
    estimated_audience: int | None
    estimated_send_volume: dict[str, int] | None
    estimated_cost_cents: int | None
    estimate_basis: str
    generated_at: datetime
    items: list[CampaignLaunchChecklistItem]


class CampaignLaunchChecklistService:
    """Builds a launch-readiness report for a saved workflow or draft preview."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        definition_dict: dict[str, Any] | None = None,
        location_id: str | None = None,
    ) -> CampaignLaunchChecklist:
        effective_definition = definition_dict or workflow.definition
        effective_location_id = location_id if location_id is not None else workflow.location_id
        location_id_text = str(effective_location_id) if effective_location_id else None

        items: list[CampaignLaunchChecklistItem] = []
        issues = await WorkflowValidationService(
            self.session,
            content_validator=ContentComplianceValidator(),
            readiness_checker=ChannelReadinessService(self.session),
        ).validate(
            effective_definition or {},
            institution_id=institution_id,
            location_id=location_id_text,
        )
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        items.append(
            CampaignLaunchChecklistItem(
                id="workflow_validation",
                section="workflow",
                label="Workflow structure",
                status="blocked" if errors else "warning" if warnings else "pass",
                message=(
                    f"{len(errors)} blocking validation issue(s), {len(warnings)} warning(s)."
                    if errors or warnings
                    else "Workflow schema, graph links, and server validation pass."
                ),
                fix_href="#validation",
                metadata={
                    "errors": [_issue_payload(i) for i in errors],
                    "warnings": [_issue_payload(i) for i in warnings],
                },
            )
        )

        try:
            definition = WorkflowDefinition.model_validate(effective_definition or {})
        except Exception:
            return self._finalize(
                workflow=workflow,
                location_id=location_id_text,
                items=items,
                estimated_audience=None,
                estimated_send_volume=None,
                estimated_cost_cents=None,
                estimate_basis="Definition is invalid; estimates are unavailable.",
            )

        send_nodes = [n for n in definition.nodes if isinstance(n, _SEND_NODE_TYPES)]
        items += self._merge_field_items(warnings)
        items += await self._channel_items(
            definition,
            institution_id=institution_id,
            location_id=location_id_text,
        )
        items += self._compliance_items(definition, send_nodes, errors, warnings)
        items += self._quiet_hours_item(send_nodes)
        items += await self._nexhealth_items(
            definition,
            institution_id=institution_id,
            location_id=location_id_text,
        )
        items += self._handoff_items(definition)

        audience_status, audience_message = self._audience_status(definition)
        items.append(
            CampaignLaunchChecklistItem(
                id="audience_estimate",
                section="audience",
                label="Audience estimate and exclusions",
                status=audience_status,
                message=audience_message,
                fix_href="/institution-admin/campaigns/audience",
                metadata={"trigger_type": definition.trigger.type},
            )
        )

        per_contact = _planned_sends_per_contact(send_nodes)
        estimated_send_volume: dict[str, int] | None = None
        estimated_cost_cents: int | None = None
        estimate_basis = (
            "Audience preview is not available yet; showing planned sends per enrolled contact."
        )
        items.append(
            CampaignLaunchChecklistItem(
                id="send_volume_cost",
                section="estimates",
                label="Estimated send volume and cost",
                status="unknown",
                message=(
                    "Exact send volume and projected spend need an audience count. "
                    f"Per enrolled contact, this workflow can attempt {_format_volume(per_contact)}."
                ),
                fix_href="/institution-admin/campaigns/audience",
                metadata={"planned_sends_per_contact": per_contact},
            )
        )

        return self._finalize(
            workflow=workflow,
            location_id=location_id_text,
            items=items,
            estimated_audience=None,
            estimated_send_volume=estimated_send_volume,
            estimated_cost_cents=estimated_cost_cents,
            estimate_basis=estimate_basis,
        )

    def _finalize(
        self,
        *,
        workflow: AutomationWorkflow,
        location_id: str | None,
        items: list[CampaignLaunchChecklistItem],
        estimated_audience: int | None,
        estimated_send_volume: dict[str, int] | None,
        estimated_cost_cents: int | None,
        estimate_basis: str,
    ) -> CampaignLaunchChecklist:
        blockers = sum(1 for i in items if i.status == "blocked")
        warnings = sum(1 for i in items if i.status == "warning")
        unknowns = sum(1 for i in items if i.status == "unknown")
        overall: ChecklistStatus
        if blockers:
            overall = "blocked"
        elif warnings or unknowns:
            overall = "warning"
        else:
            overall = "pass"
        return CampaignLaunchChecklist(
            workflow_id=str(workflow.id),
            workflow_version_id=(
                str(workflow.current_version_id) if workflow.current_version_id else None
            ),
            location_id=location_id,
            overall_status=overall,
            blockers_count=blockers,
            warnings_count=warnings,
            unknown_count=unknowns,
            estimated_audience=estimated_audience,
            estimated_send_volume=estimated_send_volume,
            estimated_cost_cents=estimated_cost_cents,
            estimate_basis=estimate_basis,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

    @staticmethod
    def _merge_field_items(warnings: list[Any]) -> list[CampaignLaunchChecklistItem]:
        merge_warnings = [i for i in warnings if (i.code or "").startswith("merge_field_")]
        if not merge_warnings:
            return [
                CampaignLaunchChecklistItem(
                    id="merge_fields",
                    section="content",
                    label="Merge-field readiness",
                    status="pass",
                    message="All detected merge fields are known for the selected trigger and channel.",
                    fix_href="#message-editor",
                )
            ]
        return [
            CampaignLaunchChecklistItem(
                id="merge_fields",
                section="content",
                label="Merge-field readiness",
                status="warning",
                message=f"{len(merge_warnings)} merge-field warning(s) need review.",
                fix_href="#message-editor",
                metadata={"warnings": [_issue_payload(i) for i in merge_warnings]},
            )
        ]

    async def _channel_items(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[CampaignLaunchChecklistItem]:
        used_channels = _channels_used(definition)
        if not used_channels:
            return [
                CampaignLaunchChecklistItem(
                    id="channel_provisioning",
                    section="channels",
                    label="Channel provisioning",
                    status="pass",
                    message="No SMS, email, or voice send steps are configured.",
                    fix_href="/institution-admin/settings",
                )
            ]
        if not location_id:
            return [
                CampaignLaunchChecklistItem(
                    id="channel_provisioning",
                    section="channels",
                    label="Channel provisioning",
                    status="unknown",
                    message="Select a location to verify SMS, email, and voice setup.",
                    fix_href="/institution-admin/settings",
                    metadata={"used_channels": sorted(used_channels)},
                )
            ]

        report = await ChannelReadinessService(self.session).readiness_for_location(
            institution_id=institution_id,
            location_id=location_id,
        )
        ready = {
            "sms": report.sms,
            "email": report.email,
            "voice": report.voice_configurable,
        }
        details = [d for d in report.details if d["channel"] in used_channels]
        missing = [d for d in details if not d["ready"]]
        return [
            CampaignLaunchChecklistItem(
                id="channel_provisioning",
                section="channels",
                label="Channel provisioning",
                status="warning" if missing else "pass",
                message=(
                    f"{', '.join(d['channel'].upper() for d in missing)} setup is missing."
                    if missing
                    else "All channels used by this workflow are provisioned for the location."
                ),
                fix_href="/institution-admin/settings",
                metadata={
                    "used_channels": sorted(used_channels),
                    "ready": {k: v for k, v in ready.items() if k in used_channels},
                    "details": details,
                },
            )
        ]

    @staticmethod
    def _compliance_items(
        definition: WorkflowDefinition,
        send_nodes: list[Any],
        errors: list[Any],
        warnings: list[Any],
    ) -> list[CampaignLaunchChecklistItem]:
        content_class = definition.compliance.content_class if definition.compliance else None
        consent_required = (
            definition.compliance.consent_required if definition.compliance else None
        )
        compliance_errors = [
            i
            for i in errors
            if i.code
            in {"consent_required", "promotional_in_exempt_class", "phi_in_body"}
        ]
        compliance_warnings = [
            i
            for i in warnings
            if i.code
            in {
                "content_class_unset",
                "sensitive_clinical_in_body",
                "ai_voice_disclosure_required",
                "ai_voice_marketing_needs_express_consent",
            }
        ]

        if compliance_errors:
            classification_status: ChecklistStatus = "blocked"
            classification_msg = f"{len(compliance_errors)} compliance issue(s) block launch."
        elif compliance_warnings:
            classification_status = "warning"
            classification_msg = f"{len(compliance_warnings)} compliance warning(s) need review."
        elif send_nodes:
            classification_status = "pass"
            classification_msg = f"Content class is {content_class}; consent_required={consent_required}."
        else:
            classification_status = "pass"
            classification_msg = "No outbound send steps require content classification."

        suppression_status: ChecklistStatus = "pass"
        suppression_msg = "Send-time DNC, opt-out suppression, and channel consent gates are enforced."
        if send_nodes and content_class is None:
            suppression_status = "warning"
            suppression_msg = "Set a content class so channel consent basis is explicit before launch."
        elif send_nodes and consent_required is False:
            suppression_status = "warning"
            suppression_msg = "Consent records are not required by this definition; suppression/DNC still applies."

        sms_nodes = [n for n in send_nodes if isinstance(n, SendSmsNode)]
        stop_help_status: ChecklistStatus = "pass"
        stop_help_msg = _SMS_STOP_HELP_COPY if sms_nodes else "No SMS steps require STOP/HELP footer copy."

        return [
            CampaignLaunchChecklistItem(
                id="compliance_classification",
                section="compliance",
                label="Compliance classification",
                status=classification_status,
                message=classification_msg,
                fix_href="#compliance",
                metadata={
                    "content_class": content_class,
                    "consent_required": consent_required,
                    "errors": [_issue_payload(i) for i in compliance_errors],
                    "warnings": [_issue_payload(i) for i in compliance_warnings],
                },
            ),
            CampaignLaunchChecklistItem(
                id="consent_suppression",
                section="compliance",
                label="Consent and suppression coverage",
                status=suppression_status,
                message=suppression_msg,
                fix_href="#compliance",
            ),
            CampaignLaunchChecklistItem(
                id="sms_stop_help_copy",
                section="compliance",
                label="SMS STOP/HELP copy",
                status=stop_help_status,
                message=stop_help_msg,
                fix_href="#message-editor",
            ),
        ]

    @staticmethod
    def _quiet_hours_item(send_nodes: list[Any]) -> list[CampaignLaunchChecklistItem]:
        quiet_off = [
            n.id
            for n in send_nodes
            if getattr(n, "respect_quiet_hours", True) is False
        ]
        if quiet_off:
            return [
                CampaignLaunchChecklistItem(
                    id="quiet_hours",
                    section="compliance",
                    label="Quiet hours and send windows",
                    status="warning",
                    message="Some send steps bypass quiet-hour scheduling.",
                    fix_href="#message-editor",
                    metadata={"node_ids": quiet_off},
                )
            ]
        return [
            CampaignLaunchChecklistItem(
                id="quiet_hours",
                section="compliance",
                label="Quiet hours and send windows",
                status="pass",
                message="Send steps respect the location quiet-hours gate.",
                fix_href="#message-editor",
            )
        ]

    async def _nexhealth_items(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[CampaignLaunchChecklistItem]:
        if definition.trigger.type not in _APPOINTMENT_TRIGGER_TYPES:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="pass",
                    message="This trigger does not require NexHealth appointment data.",
                    fix_href="/institution-admin/settings",
                )
            ]
        if not location_id:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="blocked",
                    message="Appointment and recall triggers require a location with NexHealth configuration.",
                    fix_href="/institution-admin/settings",
                )
            ]

        location = await self.session.get(InstitutionLocation, location_id)
        if not location or not location.nexhealth_subdomain or not location.nexhealth_location_id:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="blocked",
                    message="Location is missing NexHealth subdomain or location id.",
                    fix_href="/institution-admin/settings",
                )
            ]

        if definition.trigger.type == "recall_scan":
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth recall capability",
                    status="blocked",
                    message="Recall audience generation is not available until the audience/segmentation adapter lands.",
                    fix_href="/institution-admin/campaigns/audience",
                )
            ]

        subscription = await self._subscription(institution_id, location_id)
        if subscription is None:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="warning",
                    message="No local NexHealth appointment webhook subscription row exists for this location.",
                    fix_href="/institution-admin/settings",
                )
            ]
        if subscription.status != NexHealthWebhookSubscriptionStatus.ACTIVE.value:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="warning",
                    message=f"NexHealth webhook subscription is {subscription.status}.",
                    fix_href="/institution-admin/settings",
                    metadata={"subscription_id": str(subscription.id)},
                )
            ]

        newest = await self._newest_projection_sync(institution_id, location_id)
        if newest is None:
            return [
                CampaignLaunchChecklistItem(
                    id="nexhealth_readiness",
                    section="data",
                    label="NexHealth data freshness",
                    status="unknown",
                    message="Webhook subscription is active, but no appointment projection rows are available yet.",
                    fix_href="/institution-admin/settings",
                    metadata={"subscription_id": str(subscription.id)},
                )
            ]
        newest = _as_utc(newest)
        age = datetime.now(timezone.utc) - newest
        return [
            CampaignLaunchChecklistItem(
                id="nexhealth_readiness",
                section="data",
                label="NexHealth data freshness",
                status="warning" if age > _FRESHNESS_WINDOW else "pass",
                message=(
                    "Appointment projection is stale; live send-time revalidation still runs."
                    if age > _FRESHNESS_WINDOW
                    else "Appointment webhook subscription and projection freshness look current."
                ),
                fix_href="/institution-admin/settings",
                metadata={
                    "subscription_id": str(subscription.id),
                    "last_synced_at": newest.isoformat(),
                    "freshness_window_hours": int(_FRESHNESS_WINDOW.total_seconds() / 3600),
                },
            )
        ]

    def _handoff_items(self, definition: WorkflowDefinition) -> list[CampaignLaunchChecklistItem]:
        voice_nodes = [n for n in definition.nodes if isinstance(n, SendVoiceNode)]
        waits_for_outcome = [n.id for n in voice_nodes if n.wait_for_outcome]
        if not voice_nodes:
            return [
                CampaignLaunchChecklistItem(
                    id="staff_handoff",
                    section="operations",
                    label="Staff handoff/failure routing",
                    status="pass",
                    message="No AI voice handoff path is required for this workflow.",
                    fix_href="/institution-admin/callbacks",
                )
            ]
        if waits_for_outcome:
            return [
                CampaignLaunchChecklistItem(
                    id="staff_handoff",
                    section="operations",
                    label="Staff handoff/failure routing",
                    status="pass",
                    message="Voice outcome feedback is enabled for at least one voice step.",
                    fix_href="/institution-admin/callbacks",
                    metadata={"node_ids": waits_for_outcome},
                )
            ]
        return [
            CampaignLaunchChecklistItem(
                id="staff_handoff",
                section="operations",
                label="Staff handoff/failure routing",
                status="warning",
                message="Voice steps are fire-and-forget; confirm staff monitors failed/needs-callback calls.",
                fix_href="/institution-admin/callbacks",
                metadata={"node_ids": [n.id for n in voice_nodes]},
            )
        ]

    @staticmethod
    def _audience_status(definition: WorkflowDefinition) -> tuple[ChecklistStatus, str]:
        trigger_type = definition.trigger.type
        if trigger_type in _BROAD_TRIGGER_TYPES:
            return (
                "blocked",
                "Broad campaign audience size is unknown until audience preview is available.",
            )
        if trigger_type in {"manual", "bulk_import"}:
            return (
                "warning",
                "Audience is selected at enrollment/import time; preview exclusions are not available yet.",
            )
        return (
            "unknown",
            "This event-triggered campaign has no fixed audience before matching events arrive.",
        )

    async def _subscription(
        self, institution_id: str, location_id: str
    ) -> NexHealthWebhookSubscription | None:
        result = await self.session.execute(
            select(NexHealthWebhookSubscription)
            .where(
                NexHealthWebhookSubscription.institution_id == institution_id,
                NexHealthWebhookSubscription.location_id == location_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _newest_projection_sync(
        self, institution_id: str, location_id: str
    ) -> datetime | None:
        result = await self.session.execute(
            select(func.max(AppointmentWorkingSet.last_synced_at)).where(
                AppointmentWorkingSet.institution_id == institution_id,
                AppointmentWorkingSet.location_id == location_id,
            )
        )
        return result.scalar_one_or_none()


def _issue_payload(issue: Any) -> dict[str, Any]:
    return {
        "severity": issue.severity,
        "node_id": issue.node_id,
        "field_path": list(issue.field_path),
        "message": issue.message,
        "code": issue.code,
    }


def _channels_used(definition: WorkflowDefinition) -> set[str]:
    channels: set[str] = set()
    for node in definition.nodes:
        if isinstance(node, SendSmsNode):
            channels.add("sms")
        elif isinstance(node, SendEmailNode):
            channels.add("email")
        elif isinstance(node, SendVoiceNode):
            channels.add("voice")
    return channels


def _planned_sends_per_contact(send_nodes: list[Any]) -> dict[str, int]:
    volume = {"sms": 0, "email": 0, "voice": 0}
    for node in send_nodes:
        attempts = int(getattr(node, "max_attempts", 1) or 1)
        if isinstance(node, SendSmsNode):
            volume["sms"] += attempts
        elif isinstance(node, SendEmailNode):
            volume["email"] += attempts
        elif isinstance(node, SendVoiceNode):
            volume["voice"] += attempts
    return {k: v for k, v in volume.items() if v > 0}


def _format_volume(volume: dict[str, int]) -> str:
    if not volume:
        return "0 sends"
    return ", ".join(f"{count} {channel}" for channel, count in volume.items())


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
