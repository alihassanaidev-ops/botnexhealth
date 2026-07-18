"""Audience preview and constrained segmentation for campaigns."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflow,
    AutomationWorkflowRun,
)
from src.app.models.campaign_audience import (
    CampaignAudienceDefinition,
    CampaignAudiencePreview,
)
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.sms_consent import (
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentStatus,
    SmsSuppression,
)
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.merge_field_catalog import fields_for
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.sms_privacy import hash_email, hash_phone, mask_email, mask_phone

AudienceChannel = Literal["sms", "email", "voice"]

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_ACTIVE_RUN_STATUSES = (
    AutomationRunStatus.PENDING.value,
    AutomationRunStatus.RUNNING.value,
    AutomationRunStatus.WAITING.value,
)
_MARKETING_CONTENT_CLASSES = {"sales", "marketing"}
_ALL_BASES = frozenset(b.value for b in ConsentBasis)
_PREVIEW_TTL = timedelta(minutes=30)
_MAX_CANDIDATES = 5000
_DEFAULT_SAMPLE_LIMIT = 25


class AudienceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_no_future_appointment: bool = False
    recall_due_before: date | None = None
    last_visit_before: date | None = None
    appointment_type_id_in: list[str] = Field(default_factory=list)
    provider_id_in: list[str] = Field(default_factory=list)
    location_id_in: list[str] = Field(default_factory=list)
    preferred_language_in: list[str] = Field(default_factory=list)
    contact_channel_available: list[AudienceChannel] = Field(default_factory=list)

    @field_validator(
        "appointment_type_id_in",
        "provider_id_in",
        "location_id_in",
        "preferred_language_in",
        mode="before",
    )
    @classmethod
    def _string_list(cls, value: object) -> object:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("contact_channel_available", mode="before")
    @classmethod
    def _channel_list(cls, value: object) -> object:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        return value


class AudienceExclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_consent: bool = True
    do_not_contact: bool = True
    suppressed: bool = True
    contacted_within_days: int | None = Field(default=1, ge=0, le=365)
    max_contacts_per_rolling_7_days: int | None = Field(default=3, ge=1, le=30)
    already_enrolled_active: bool = True
    already_booked: bool = True
    missing_required_merge_context: bool = True


class AudienceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: AudienceFilters = Field(default_factory=AudienceFilters)
    exclusions: AudienceExclusions = Field(default_factory=AudienceExclusions)

    @model_validator(mode="before")
    @classmethod
    def _allow_legacy_split(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {
            "filters": value.get("filters") or {},
            "exclusions": value.get("exclusions") or {},
        }


@dataclass(frozen=True)
class AudienceSample:
    contact_id: str
    display_name: str | None
    phone_masked: str | None
    email_masked: str | None
    status: Literal["included", "excluded"]
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AudiencePreviewResult:
    preview_id: str
    workflow_id: str
    workflow_version_id: str | None
    location_id: str | None
    segment: dict[str, Any]
    exclusions: dict[str, Any]
    total_candidates: int
    included_count: int
    excluded_count: int
    counts_by_reason: dict[str, int]
    samples: list[AudienceSample]
    warnings: list[str]
    estimate_basis: str
    generated_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AudienceEnrollResult:
    workflow_id: str
    workflow_version_id: str
    preview_id: str
    enqueued: int
    skipped: int
    counts_by_reason: dict[str, int]


class CampaignAudienceService:
    """Builds and persists audience previews from local campaign-safe projections."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_definition(
        self, *, institution_id: str, workflow_id: str
    ) -> CampaignAudienceDefinition | None:
        result = await self.session.execute(
            select(CampaignAudienceDefinition).where(
                CampaignAudienceDefinition.institution_id == institution_id,
                CampaignAudienceDefinition.workflow_id == workflow_id,
            )
        )
        row = result.scalar_one_or_none()
        if inspect.isawaitable(row):
            close = getattr(row, "close", None)
            if callable(close):
                close()
            return None
        return row

    async def upsert_definition(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        segment: AudienceSegment,
        actor_user_id: str | None,
    ) -> CampaignAudienceDefinition:
        existing = await self.get_definition(
            institution_id=institution_id, workflow_id=str(workflow.id)
        )
        payload = segment.model_dump(mode="json")
        if existing is None:
            existing = CampaignAudienceDefinition(
                institution_id=institution_id,
                location_id=str(workflow.location_id) if workflow.location_id else None,
                workflow_id=str(workflow.id),
                segment=payload["filters"],
                exclusions=payload["exclusions"],
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            self.session.add(existing)
        else:
            existing.location_id = str(workflow.location_id) if workflow.location_id else None
            existing.segment = payload["filters"]
            existing.exclusions = payload["exclusions"]
            existing.updated_by_user_id = actor_user_id
        await self.session.flush()
        return existing

    async def preview(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        segment: AudienceSegment | None = None,
        actor_user_id: str | None = None,
        sample_limit: int = _DEFAULT_SAMPLE_LIMIT,
    ) -> AudiencePreviewResult:
        definition_row = await self.get_definition(
            institution_id=institution_id, workflow_id=str(workflow.id)
        )
        if segment is None:
            segment = _segment_from_definition(definition_row)
        segment = segment or AudienceSegment()
        workflow_definition = _definition_or_none(workflow.definition)
        channels = _channels_for_preview(segment, workflow_definition)
        required_context = _required_context_missing_reasons(workflow_definition)
        generated_at = datetime.now(timezone.utc)
        expires_at = generated_at + _PREVIEW_TTL

        candidates = await self._candidate_contacts(
            institution_id=institution_id,
            location_ids=_effective_location_ids(segment, workflow),
        )

        included: list[AudienceSample] = []
        excluded: list[AudienceSample] = []
        counts_by_reason: dict[str, int] = {}
        included_count = 0
        excluded_count = 0

        for contact in candidates:
            reasons = await self._exclusion_reasons(
                contact,
                workflow=workflow,
                institution_id=institution_id,
                segment=segment,
                channels=channels,
                content_class=_content_class(workflow_definition),
                required_context_missing=required_context,
                now=generated_at,
            )
            if reasons:
                excluded_count += 1
                for reason in reasons:
                    counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1
                if len(excluded) < sample_limit:
                    excluded.append(_sample(contact, "excluded", reasons))
            else:
                included_count += 1
                if len(included) < sample_limit:
                    included.append(_sample(contact, "included", []))

        preview = CampaignAudiencePreview(
            institution_id=institution_id,
            location_id=str(workflow.location_id) if workflow.location_id else None,
            workflow_id=str(workflow.id),
            workflow_version_id=(
                str(workflow.current_version_id) if workflow.current_version_id else None
            ),
            segment=segment.model_dump(mode="json")["filters"],
            exclusions=segment.model_dump(mode="json")["exclusions"],
            counts_by_reason=counts_by_reason,
            included_count=included_count,
            excluded_count=excluded_count,
            created_by_user_id=actor_user_id,
            expires_at=expires_at,
        )
        self.session.add(preview)
        await self.session.flush()

        warnings = _preview_warnings(segment, workflow_definition, definition_row)
        return AudiencePreviewResult(
            preview_id=str(preview.id),
            workflow_id=str(workflow.id),
            workflow_version_id=(
                str(workflow.current_version_id) if workflow.current_version_id else None
            ),
            location_id=str(workflow.location_id) if workflow.location_id else None,
            segment=preview.segment,
            exclusions=preview.exclusions,
            total_candidates=len(candidates),
            included_count=preview.included_count,
            excluded_count=preview.excluded_count,
            counts_by_reason=counts_by_reason,
            samples=included + excluded,
            warnings=warnings,
            estimate_basis="Computed from local contacts and appointment working-set projections; send-time gates still revalidate.",
            generated_at=generated_at,
            expires_at=expires_at,
        )

    async def enqueue_enrollment(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        segment: AudienceSegment | None,
        actor_user_id: str | None,
        preview_id: str | None = None,
        max_enrollments: int = 500,
    ) -> AudienceEnrollResult:
        from src.app.tasks.automation_workflow import enroll_and_start_workflow_run

        if not workflow.current_version_id:
            raise ValueError("workflow has no published version")

        if segment is None and preview_id:
            preview = await self._load_preview(
                institution_id=institution_id,
                workflow_id=str(workflow.id),
                preview_id=preview_id,
            )
            segment = AudienceSegment(
                filters=preview.segment,
                exclusions=preview.exclusions,
            )
        elif segment is None:
            segment = _segment_from_definition(
                await self.get_definition(institution_id=institution_id, workflow_id=str(workflow.id))
            )

        result = await self.preview(
            workflow,
            institution_id=institution_id,
            segment=segment,
            actor_user_id=actor_user_id,
            sample_limit=0,
        )

        contacts = await self._included_contact_ids(
            workflow,
            institution_id=institution_id,
            segment=segment,
            max_enrollments=max_enrollments,
        )
        version_id = str(workflow.current_version_id)
        for contact_id in contacts:
            enroll_and_start_workflow_run.apply_async(
                kwargs={
                    "institution_id": institution_id,
                    "workflow_id": str(workflow.id),
                    "workflow_version_id": version_id,
                    "contact_id": contact_id,
                    "location_id": str(workflow.location_id) if workflow.location_id else None,
                    "trigger_type": workflow.trigger_type,
                    "trigger_ref_type": "audience_preview",
                    "trigger_ref_id": result.preview_id,
                    "idempotency_key": f"audience:{result.preview_id}:{contact_id}",
                    "trigger_metadata": {
                        "source": "audience_preview",
                        "audience_preview_id": result.preview_id,
                        "audience_segment_hash": _segment_hash(segment),
                    },
                },
                queue="workflow",
            )

        return AudienceEnrollResult(
            workflow_id=str(workflow.id),
            workflow_version_id=version_id,
            preview_id=result.preview_id,
            enqueued=len(contacts),
            skipped=max(result.included_count - len(contacts), 0),
            counts_by_reason=result.counts_by_reason,
        )

    async def _load_preview(
        self, *, institution_id: str, workflow_id: str, preview_id: str
    ) -> CampaignAudiencePreview:
        result = await self.session.execute(
            select(CampaignAudiencePreview).where(
                CampaignAudiencePreview.id == preview_id,
                CampaignAudiencePreview.institution_id == institution_id,
                CampaignAudiencePreview.workflow_id == workflow_id,
                CampaignAudiencePreview.expires_at > datetime.now(timezone.utc),
            )
        )
        preview = result.scalar_one_or_none()
        if preview is None:
            raise ValueError("preview not found or expired")
        return preview

    async def _candidate_contacts(
        self, *, institution_id: str, location_ids: list[str]
    ) -> list[Contact]:
        stmt = (
            select(Contact)
            .where(
                Contact.institution_id == institution_id,
                Contact.merged_into_id.is_(None),
                Contact.anonymized_at.is_(None),
            )
            .order_by(Contact.created_at.desc(), Contact.id.desc())
            .limit(_MAX_CANDIDATES)
        )
        contacts = (await self.session.execute(stmt)).scalars().all()
        if not location_ids:
            return list(contacts)
        filtered = []
        for contact in contacts:
            if await self._has_any_location(str(contact.id), institution_id, location_ids):
                filtered.append(contact)
        return filtered

    async def _included_contact_ids(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        segment: AudienceSegment,
        max_enrollments: int,
    ) -> list[str]:
        workflow_definition = _definition_or_none(workflow.definition)
        channels = _channels_for_preview(segment, workflow_definition)
        required_context = _required_context_missing_reasons(workflow_definition)
        contacts = await self._candidate_contacts(
            institution_id=institution_id,
            location_ids=_effective_location_ids(segment, workflow),
        )
        ids: list[str] = []
        now = datetime.now(timezone.utc)
        for contact in contacts:
            reasons = await self._exclusion_reasons(
                contact,
                workflow=workflow,
                institution_id=institution_id,
                segment=segment,
                channels=channels,
                content_class=_content_class(workflow_definition),
                required_context_missing=required_context,
                now=now,
            )
            if not reasons:
                ids.append(str(contact.id))
                if len(ids) >= max_enrollments:
                    break
        return ids

    async def _exclusion_reasons(
        self,
        contact: Contact,
        *,
        workflow: AutomationWorkflow,
        institution_id: str,
        segment: AudienceSegment,
        channels: set[str],
        content_class: str | None,
        required_context_missing: set[str],
        now: datetime,
    ) -> list[str]:
        reasons: list[str] = []
        filters = segment.filters
        exclusions = segment.exclusions
        contact_id = str(contact.id)

        if filters.recall_due_before is not None:
            reasons.append("missing_required_merge_context")
        if filters.preferred_language_in:
            reasons.append("missing_required_merge_context")

        if filters.has_no_future_appointment and await self._has_future_appointment(
            contact, institution_id=institution_id, now=now
        ):
            reasons.append("already_booked")

        if exclusions.already_booked and await self._has_future_appointment(
            contact, institution_id=institution_id, now=now
        ):
            reasons.append("already_booked")

        if filters.last_visit_before is not None:
            last_visit = await self._last_visit_at(contact, institution_id=institution_id, now=now)
            if last_visit is None:
                reasons.append("missing_required_merge_context")
            elif last_visit.date() >= filters.last_visit_before:
                reasons.append("last_visit_filter_mismatch")

        if filters.appointment_type_id_in or filters.provider_id_in:
            if not await self._matches_appointment_filters(
                contact,
                institution_id=institution_id,
                appointment_type_ids=filters.appointment_type_id_in,
                provider_ids=filters.provider_id_in,
                now=now,
            ):
                reasons.append("appointment_filter_mismatch")

        if exclusions.already_enrolled_active and await self._has_active_run(
            institution_id=institution_id,
            workflow_id=str(workflow.id),
            contact_id=contact_id,
        ):
            reasons.append("already_enrolled_active")

        if exclusions.missing_required_merge_context and required_context_missing:
            reasons.append("missing_required_merge_context")

        if filters.contact_channel_available:
            unavailable = [
                channel for channel in filters.contact_channel_available
                if not _has_channel_identifier(contact, channel)
            ]
            if unavailable:
                reasons.append("contact_channel_unavailable")

        if exclusions.do_not_contact and await self._is_dnc(
            contact,
            institution_id=institution_id,
            location_id=str(workflow.location_id) if workflow.location_id else None,
        ):
            reasons.append("do_not_contact")

        if exclusions.suppressed and await self._is_suppressed(contact, institution_id=institution_id):
            reasons.append("suppressed")

        if exclusions.no_consent and channels:
            allowed = False
            for channel in channels:
                if _has_channel_identifier(contact, channel) and await self._has_usable_consent(
                    contact,
                    institution_id=institution_id,
                    channel=channel,
                    content_class=content_class,
                ):
                    allowed = True
                    break
            if not allowed:
                reasons.append("no_consent")

        if await self._frequency_capped(
            contact_id,
            institution_id=institution_id,
            days=exclusions.contacted_within_days,
            max_rolling_7=exclusions.max_contacts_per_rolling_7_days,
            now=now,
        ):
            reasons.append("contacted_recently")

        return list(dict.fromkeys(reasons))

    async def _has_any_location(
        self, contact_id: str, institution_id: str, location_ids: list[str]
    ) -> bool:
        access = await self.session.execute(
            select(ContactLocationAccess.id).where(
                ContactLocationAccess.institution_id == institution_id,
                ContactLocationAccess.contact_id == contact_id,
                ContactLocationAccess.location_id.in_(location_ids),
            ).limit(1)
        )
        if access.first() is not None:
            return True
        appt = await self.session.execute(
            select(AppointmentWorkingSet.id).where(
                AppointmentWorkingSet.institution_id == institution_id,
                AppointmentWorkingSet.contact_id == contact_id,
                AppointmentWorkingSet.location_id.in_(location_ids),
            ).limit(1)
        )
        return appt.first() is not None

    async def _has_future_appointment(
        self, contact: Contact, *, institution_id: str, now: datetime
    ) -> bool:
        result = await self.session.execute(
            select(AppointmentWorkingSet.id)
            .where(
                AppointmentWorkingSet.institution_id == institution_id,
                _appointment_contact_clause(contact),
                AppointmentWorkingSet.status == "scheduled",
                AppointmentWorkingSet.start_time >= now,
            )
            .limit(1)
        )
        return result.first() is not None

    async def _last_visit_at(
        self, contact: Contact, *, institution_id: str, now: datetime
    ) -> datetime | None:
        result = await self.session.execute(
            select(func.max(AppointmentWorkingSet.start_time)).where(
                AppointmentWorkingSet.institution_id == institution_id,
                _appointment_contact_clause(contact),
                AppointmentWorkingSet.status == "scheduled",
                AppointmentWorkingSet.start_time < now,
            )
        )
        return result.scalar_one_or_none()

    async def _matches_appointment_filters(
        self,
        contact: Contact,
        *,
        institution_id: str,
        appointment_type_ids: list[str],
        provider_ids: list[str],
        now: datetime,
    ) -> bool:
        conditions = [
            AppointmentWorkingSet.institution_id == institution_id,
            _appointment_contact_clause(contact),
            AppointmentWorkingSet.status == "scheduled",
            AppointmentWorkingSet.start_time >= now,
        ]
        if appointment_type_ids:
            conditions.append(AppointmentWorkingSet.appointment_type_id.in_(appointment_type_ids))
        if provider_ids:
            conditions.append(AppointmentWorkingSet.provider_id.in_(provider_ids))
        result = await self.session.execute(
            select(AppointmentWorkingSet.id).where(and_(*conditions)).limit(1)
        )
        return result.first() is not None

    async def _has_active_run(
        self, *, institution_id: str, workflow_id: str, contact_id: str
    ) -> bool:
        result = await self.session.execute(
            select(AutomationWorkflowRun.id).where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.workflow_id == workflow_id,
                AutomationWorkflowRun.contact_id == contact_id,
                AutomationWorkflowRun.status.in_(_ACTIVE_RUN_STATUSES),
            ).limit(1)
        )
        return result.first() is not None

    async def _is_dnc(
        self, contact: Contact, *, institution_id: str, location_id: str | None
    ) -> bool:
        phone_hash = hash_phone(contact.phone) if contact.phone else None
        return await SmsComplianceService(self.session).is_do_not_contact(
            institution_id=institution_id,
            location_id=location_id,
            phone_hash=phone_hash,
            contact_id=str(contact.id),
        )

    async def _is_suppressed(self, contact: Contact, *, institution_id: str) -> bool:
        phone_hash = hash_phone(contact.phone) if contact.phone else None
        conditions = [SmsSuppression.contact_id == str(contact.id)]
        if phone_hash:
            conditions.append(SmsSuppression.phone_hash == phone_hash)
        result = await self.session.execute(
            select(SmsSuppression.id).where(
                SmsSuppression.institution_id == institution_id,
                SmsSuppression.channel == ConsentChannel.SMS.value,
                SmsSuppression.is_active.is_(True),
                or_(*conditions),
            ).limit(1)
        )
        return result.first() is not None

    async def _has_usable_consent(
        self,
        contact: Contact,
        *,
        institution_id: str,
        channel: str,
        content_class: str | None,
    ) -> bool:
        if content_class in (None, "transactional_care") and channel == "sms":
            return await self._latest_consent_allows(
                contact, institution_id=institution_id, channel=channel, content_class=content_class
            )
        return await self._latest_consent_allows(
            contact, institution_id=institution_id, channel=channel, content_class=content_class
        )

    async def _latest_consent_allows(
        self,
        contact: Contact,
        *,
        institution_id: str,
        channel: str,
        content_class: str | None,
    ) -> bool:
        identity_hash = hash_email(contact.email) if channel == "email" else hash_phone(contact.phone)
        if not identity_hash:
            return False
        stmt = select(ConsentRecord).where(
            ConsentRecord.institution_id == institution_id,
            ConsentRecord.channel == channel,
        )
        if channel == "email":
            stmt = stmt.where(ConsentRecord.email_hash == identity_hash)
        else:
            stmt = stmt.where(ConsentRecord.phone_hash == identity_hash)
        result = await self.session.execute(
            stmt.order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc()).limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return content_class in (None, "transactional_care")
        if record.status == ConsentStatus.REVOKED.value:
            return False
        acceptable = _acceptable_bases(content_class)
        basis = record.basis or ConsentBasis.IMPLIED.value
        return basis in acceptable

    async def _frequency_capped(
        self,
        contact_id: str,
        *,
        institution_id: str,
        days: int | None,
        max_rolling_7: int | None,
        now: datetime,
    ) -> bool:
        if days is not None:
            since = now - timedelta(days=days)
            recent = await self.session.execute(
                select(AutomationWorkflowRun.id).where(
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.contact_id == contact_id,
                    AutomationWorkflowRun.created_at >= since,
                ).limit(1)
            )
            if recent.first() is not None:
                return True
        if max_rolling_7 is not None:
            since = now - timedelta(days=7)
            count = await self.session.execute(
                select(func.count(AutomationWorkflowRun.id)).where(
                    AutomationWorkflowRun.institution_id == institution_id,
                    AutomationWorkflowRun.contact_id == contact_id,
                    AutomationWorkflowRun.created_at >= since,
                )
            )
            if int(count.scalar_one() or 0) >= max_rolling_7:
                return True
        return False


def _segment_from_definition(row: CampaignAudienceDefinition | None) -> AudienceSegment:
    if row is None:
        return AudienceSegment()
    return AudienceSegment(filters=row.segment or {}, exclusions=row.exclusions or {})


def _effective_location_ids(
    segment: AudienceSegment, workflow: AutomationWorkflow
) -> list[str]:
    if segment.filters.location_id_in:
        return segment.filters.location_id_in
    if workflow.location_id:
        return [str(workflow.location_id)]
    return []


def _definition_or_none(definition: dict[str, Any] | None) -> WorkflowDefinition | None:
    if not definition:
        return None
    try:
        return WorkflowDefinition.model_validate(definition)
    except Exception:
        return None


def _channels_for_preview(
    segment: AudienceSegment, definition: WorkflowDefinition | None
) -> set[str]:
    explicit = set(segment.filters.contact_channel_available)
    if explicit:
        return explicit
    if definition is None:
        return set()
    channels: set[str] = set()
    for node in definition.nodes:
        if isinstance(node, SendSmsNode):
            channels.add("sms")
        elif isinstance(node, SendEmailNode):
            channels.add("email")
        elif isinstance(node, SendVoiceNode):
            channels.add("voice")
    return channels


def _content_class(definition: WorkflowDefinition | None) -> str | None:
    if definition and definition.compliance:
        return definition.compliance.content_class
    return None


def _required_context_missing_reasons(definition: WorkflowDefinition | None) -> set[str]:
    if definition is None:
        return set()
    tokens = _tokens_used(definition)
    if not tokens:
        return set()
    trigger_type = definition.trigger.type
    required = {
        field.name
        for field in fields_for(trigger_type=trigger_type, include_unavailable=True)
        if field.availability == "required_context"
    }
    contact_derived = {"patient_first_name", "patient_last_name", "patient_full_name"}
    return {token for token in tokens if token in required and token not in contact_derived}


def _tokens_used(definition: WorkflowDefinition) -> set[str]:
    tokens: set[str] = set()
    for node in definition.nodes:
        for attr in ("body_template", "subject_template"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                tokens.update(match.group(1) for match in _TOKEN_RE.finditer(value))
    return tokens


def _has_channel_identifier(contact: Contact, channel: str) -> bool:
    if channel in {"sms", "voice"}:
        return bool(contact.phone)
    if channel == "email":
        return bool(contact.email)
    return False


def _sample(
    contact: Contact,
    status: Literal["included", "excluded"],
    reasons: list[str],
) -> AudienceSample:
    return AudienceSample(
        contact_id=str(contact.id),
        display_name=contact.full_name or f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None,
        phone_masked=mask_phone(contact.phone) if contact.phone else None,
        email_masked=mask_email(contact.email) if contact.email else None,
        status=status,
        reasons=reasons,
    )


def _appointment_contact_clause(contact: Contact):
    clauses = [AppointmentWorkingSet.contact_id == str(contact.id)]
    if contact.nexhealth_patient_id:
        clauses.append(AppointmentWorkingSet.nexhealth_patient_id == str(contact.nexhealth_patient_id))
    return or_(*clauses)


def _acceptable_bases(content_class: str | None) -> frozenset[str]:
    if content_class in _MARKETING_CONTENT_CLASSES:
        return frozenset({ConsentBasis.EXPRESS_WRITTEN.value})
    if content_class == "recall":
        return frozenset({ConsentBasis.EXPRESS_WRITTEN.value, ConsentBasis.EXPRESS.value})
    return _ALL_BASES


def _preview_warnings(
    segment: AudienceSegment,
    definition: WorkflowDefinition | None,
    definition_row: CampaignAudienceDefinition | None,
) -> list[str]:
    warnings: list[str] = []
    if definition_row is None:
        warnings.append("No saved audience definition exists; using the default safe exclusions.")
    if segment.filters.recall_due_before is not None or (definition and definition.trigger.type == "recall_scan"):
        warnings.append("Recall working-set data is not available yet; recall filters are treated as missing context.")
    if segment.filters.preferred_language_in:
        warnings.append("Preferred-language projection is not available yet; language filters are treated as missing context.")
    warnings.append("NexHealth unsubscribe hints are not projected yet; local consent, suppression, and DNC remain authoritative.")
    return warnings

def _segment_hash(segment: AudienceSegment) -> str:
    payload = segment.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
