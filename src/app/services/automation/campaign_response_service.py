"""Campaign patient-response event recording and handoff creation."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.campaign_response import CampaignResponseEvent, CampaignStaffHandoff
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.outbound_voice import WorkflowVoiceAttempt
from src.app.services.automation.sms_intent_parser import SmsIntentResult, parse_sms_intent


class CampaignResponseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_sms_response(
        self,
        inbound: InboundSmsMessage,
        *,
        body: str | None,
        raw_payload: dict[str, Any] | None = None,
        parsed: SmsIntentResult | None = None,
    ) -> tuple[CampaignResponseEvent, CampaignStaffHandoff | None]:
        parsed = parsed or parse_sms_intent(body)
        source_event_id = inbound.message_sid or str(inbound.id)
        existing = await self._find_existing(
            institution_id=str(inbound.institution_id),
            channel="sms",
            source_event_id=source_event_id,
        )
        if existing is not None:
            handoff = await self._handoff_for_event(str(existing.id))
            return existing, handoff

        run = await self._load_run(inbound.workflow_run_id)
        event = CampaignResponseEvent(
            id=str(uuid4()),
            institution_id=str(inbound.institution_id),
            location_id=str(inbound.location_id) if inbound.location_id else None,
            workflow_id=str(run.workflow_id) if run else None,
            workflow_run_id=str(run.id) if run else inbound.workflow_run_id,
            contact_id=str(inbound.contact_id) if inbound.contact_id else None,
            channel="sms",
            normalized_intent=parsed.intent,
            normalized_outcome=parsed.outcome,
            source="twilio_inbound_sms",
            source_event_id=source_event_id,
            source_event_type="inbound_sms",
            confidence="deterministic",
            summary=self._sms_summary(parsed),
        )
        event.raw_body = body
        event.raw_payload = raw_payload
        self.session.add(event)
        await self.session.flush()

        if run is not None:
            self._merge_run_response_context(
                run,
                channel="sms",
                intent=parsed.intent,
                outcome=parsed.outcome,
                response_event_id=str(event.id),
                source_event_id=source_event_id,
            )

        handoff = None
        if parsed.handoff_reason:
            handoff = await self._create_handoff(
                event,
                reason=parsed.handoff_reason,
                summary=self._handoff_summary(parsed),
            )
        await self.session.flush()
        return event, handoff

    async def record_voice_response(
        self,
        *,
        institution_id: str,
        retell_call_id: str,
        call_outcome: str,
        disconnection_reason: str | None = None,
    ) -> tuple[CampaignResponseEvent, CampaignStaffHandoff | None]:
        existing = await self._find_existing(
            institution_id=institution_id,
            channel="voice",
            source_event_id=retell_call_id,
        )
        if existing is not None:
            handoff = await self._handoff_for_event(str(existing.id))
            return existing, handoff

        attempt = (
            await self.session.execute(
                select(WorkflowVoiceAttempt).where(
                    WorkflowVoiceAttempt.institution_id == institution_id,
                    WorkflowVoiceAttempt.retell_call_id == retell_call_id,
                )
            )
        ).scalar_one_or_none()
        run = await self._load_run(str(attempt.workflow_run_id) if attempt else None)
        event = CampaignResponseEvent(
            id=str(uuid4()),
            institution_id=institution_id,
            location_id=str(attempt.location_id) if attempt and attempt.location_id else None,
            workflow_id=str(run.workflow_id) if run else None,
            workflow_run_id=str(run.id) if run else None,
            contact_id=str(run.contact_id) if run and run.contact_id else None,
            channel="voice",
            normalized_intent="voice_outcome",
            normalized_outcome=call_outcome,
            source="retell_webhook",
            source_event_id=retell_call_id,
            source_event_type="call_analyzed",
            confidence="deterministic",
            summary=f"Voice outcome: {call_outcome}",
        )
        event.raw_payload = {
            "retell_call_id": retell_call_id,
            "call_outcome": call_outcome,
            "disconnection_reason": disconnection_reason,
        }
        self.session.add(event)
        await self.session.flush()

        if run is not None:
            self._merge_run_response_context(
                run,
                channel="voice",
                intent="voice_outcome",
                outcome=call_outcome,
                response_event_id=str(event.id),
                source_event_id=retell_call_id,
            )

        handoff = None
        if call_outcome in {"unknown", "failed"}:
            handoff = await self._create_handoff(
                event,
                reason="ambiguous_voice_outcome",
                summary=f"Voice call produced a {call_outcome} outcome.",
            )
        await self.session.flush()
        return event, handoff

    async def record_email_response(
        self,
        *,
        institution_id: str,
        event_type: str,
        source_event_id: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> CampaignResponseEvent:
        normalized = {
            "email.delivered": "delivered",
            "email.opened": "opened",
            "email.clicked": "clicked",
            "email.bounced": "bounced",
            "email.complained": "complained",
            "unsubscribe": "unsubscribed",
        }.get(event_type, "email_event")
        payload_key = hashlib.sha256(
            json.dumps(raw_payload or {}, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        dedupe_key = source_event_id or f"{event_type}:{payload_key}"
        existing = await self._find_existing(
            institution_id=institution_id,
            channel="email",
            source_event_id=dedupe_key,
        )
        if existing is not None:
            return existing
        event = CampaignResponseEvent(
            id=str(uuid4()),
            institution_id=institution_id,
            channel="email",
            normalized_intent=normalized,
            normalized_outcome=normalized,
            source="email_provider",
            source_event_id=dedupe_key,
            source_event_type=event_type,
            confidence="provider",
            summary=f"Email event: {normalized}",
        )
        event.raw_payload = raw_payload
        self.session.add(event)
        await self.session.flush()
        return event

    async def _load_run(self, run_id: str | None) -> AutomationWorkflowRun | None:
        if not run_id:
            return None
        return await self.session.get(AutomationWorkflowRun, run_id)

    async def _find_existing(
        self, *, institution_id: str, channel: str, source_event_id: str | None
    ) -> CampaignResponseEvent | None:
        if not source_event_id:
            return None
        return (
            await self.session.execute(
                select(CampaignResponseEvent).where(
                    CampaignResponseEvent.institution_id == institution_id,
                    CampaignResponseEvent.channel == channel,
                    CampaignResponseEvent.source_event_id == source_event_id,
                )
            )
        ).scalar_one_or_none()

    async def _handoff_for_event(self, event_id: str) -> CampaignStaffHandoff | None:
        return (
            await self.session.execute(
                select(CampaignStaffHandoff).where(
                    CampaignStaffHandoff.response_event_id == event_id
                )
            )
        ).scalar_one_or_none()

    async def _create_handoff(
        self,
        event: CampaignResponseEvent,
        *,
        reason: str,
        summary: str,
    ) -> CampaignStaffHandoff:
        handoff = CampaignStaffHandoff(
            id=str(uuid4()),
            institution_id=event.institution_id,
            location_id=event.location_id,
            workflow_id=event.workflow_id,
            workflow_run_id=event.workflow_run_id,
            contact_id=event.contact_id,
            response_event_id=str(event.id),
            reason=reason,
            status="open",
            summary=summary,
        )
        self.session.add(handoff)
        return handoff

    @staticmethod
    def _merge_run_response_context(
        run: AutomationWorkflowRun,
        *,
        channel: str,
        intent: str,
        outcome: str | None,
        response_event_id: str,
        source_event_id: str | None,
    ) -> None:
        md = dict(run.trigger_metadata or {})
        md["patient_response_channel"] = channel
        md["patient_response_intent"] = intent
        if outcome is not None:
            md["patient_response_outcome"] = outcome
        md["last_campaign_response_event_id"] = response_event_id
        if source_event_id is not None:
            md["last_campaign_response_source_event_id"] = source_event_id
        run.trigger_metadata = md

    @staticmethod
    def _sms_summary(parsed: SmsIntentResult) -> str:
        if parsed.intent == "confirm":
            return "Patient confirmed by SMS reply."
        if parsed.intent == "stop":
            return "Patient sent an SMS opt-out reply."
        if parsed.intent == "help":
            return "Patient requested SMS help information."
        if parsed.intent == "reschedule_requested":
            return "Patient requested appointment rescheduling by SMS."
        if parsed.intent == "cancel_requested":
            return "Patient requested appointment cancellation by SMS."
        return "Patient sent an SMS reply requiring review."

    @staticmethod
    def _handoff_summary(parsed: SmsIntentResult) -> str:
        if parsed.handoff_reason == "reschedule_requested":
            return "Review SMS reply and help the patient reschedule."
        if parsed.handoff_reason == "cancel_requested":
            return "Review SMS reply before cancelling or changing the appointment."
        if parsed.handoff_reason == "clinical_question":
            return "Review SMS reply for clinical content before responding."
        if parsed.handoff_reason == "billing_question":
            return "Review SMS reply for billing or insurance follow-up."
        if parsed.handoff_reason == "patient_asks_for_staff":
            return "Patient asked for staff follow-up."
        if parsed.handoff_reason == "ambiguous_response":
            return "Patient reply is ambiguous and needs staff review."
        return "Review the patient SMS reply and follow up manually."
