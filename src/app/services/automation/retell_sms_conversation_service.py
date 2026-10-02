"""Lifecycle boundary for Retell-generated SMS workflow conversations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
)
from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.campaign_response import CampaignResponseEvent, CampaignStaffHandoff
from src.app.models.contact import Contact
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.institution_location import InstitutionLocation
from src.app.models.retell_sms import (
    ACTIVE_RETELL_SMS_SESSION_STATUSES,
    RetellSmsChatProfile,
    RetellSmsSession,
    RetellSmsSessionStatus,
    RetellSmsTurn,
    RetellSmsTurnStatus,
)
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.services.automation.campaign_conversation_service import (
    CampaignConversationService,
)
from src.app.services.automation.definition_schema import RetellSmsConversationNode
from src.app.services.automation.merge_field_catalog import MergeContextBuilder
from src.app.services.automation.retell_sms_policy import (
    AUTOMATIC_RETELL_SMS_VARIABLES,
    RETELL_SMS_POLICY,
)
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService


class RetellSmsConversationConfigurationError(RuntimeError):
    """The node cannot safely start with its current tenant/profile data."""


class RetellSmsConversationBusyError(RuntimeError):
    """Another active AI session already owns this patient/location channel."""


@dataclass(frozen=True)
class RetellSmsParked:
    step: AutomationWorkflowStepExecution
    session: RetellSmsSession
    due_at: datetime


class RetellSmsConversationService:
    """Own local session state; callers own Retell/Twilio network operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enter(
        self,
        *,
        run: AutomationWorkflowRun,
        node: RetellSmsConversationNode,
        runtime: AutomationWorkflowRuntimeService,
        now: datetime | None = None,
    ) -> RetellSmsParked:
        now = now or datetime.now(timezone.utc)
        if not run.location_id or not run.contact_id:
            raise RetellSmsConversationConfigurationError(
                "retell_sms_conversation requires run location_id and contact_id"
            )

        existing = (
            await self.session.execute(
                select(RetellSmsSession).where(
                    RetellSmsSession.workflow_run_id == str(run.id),
                    RetellSmsSession.step_id == node.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            step = await self.session.get(
                AutomationWorkflowStepExecution, existing.step_execution_id
            )
            if (
                step is not None
                and existing.status in ACTIVE_RETELL_SMS_SESSION_STATUSES
            ):
                return RetellSmsParked(
                    step=step, session=existing, due_at=existing.expires_at
                )
            raise RetellSmsConversationConfigurationError(
                "retell_sms_conversation step already has a terminal session"
            )

        profile = await self.resolve_profile(
            profile_id=node.chat_profile_id,
            institution_id=str(run.institution_id),
            location_id=str(run.location_id),
        )
        conflict = (
            await self.session.execute(
                select(RetellSmsSession.id).where(
                    RetellSmsSession.institution_id == str(run.institution_id),
                    RetellSmsSession.location_id == str(run.location_id),
                    RetellSmsSession.contact_id == str(run.contact_id),
                    RetellSmsSession.status.in_(ACTIVE_RETELL_SMS_SESSION_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise RetellSmsConversationBusyError(
                "patient already has an active Retell SMS conversation at this location"
            )

        thread = await CampaignConversationService(self.session).open_sms_thread(run)
        expires_at = now + timedelta(
            seconds=RETELL_SMS_POLICY.inactivity_timeout_seconds
        )
        max_expires_at = now + timedelta(seconds=RETELL_SMS_POLICY.max_duration_seconds)
        step = await runtime.begin_step(
            run,
            step_id=node.id,
            step_type=node.type,
            scheduled_at=expires_at,
        )
        retell_session = RetellSmsSession(
            institution_id=str(run.institution_id),
            location_id=str(run.location_id),
            contact_id=str(run.contact_id),
            workflow_id=str(run.workflow_id),
            workflow_version_id=str(run.workflow_version_id),
            workflow_run_id=str(run.id),
            step_execution_id=str(step.id),
            conversation_thread_id=str(thread.id),
            chat_profile_id=str(profile.id),
            step_id=node.id,
            retell_agent_id=profile.retell_agent_id,
            # Omit agent_version from Create Chat so Retell selects the latest
            # version. Legacy profile pins are intentionally ignored.
            agent_version=None,
            status=RetellSmsSessionStatus.AWAITING_USER.value,
            expires_at=expires_at,
            max_expires_at=max_expires_at,
            last_activity_at=now,
        )
        self.session.add(retell_session)
        await self.session.flush()
        step.result_code = "awaiting_retell_sms"
        step.result_metadata = {"retell_sms_session_id": str(retell_session.id)}
        await self.session.flush()
        return RetellSmsParked(step=step, session=retell_session, due_at=expires_at)

    async def resolve_profile(
        self, *, profile_id: str, institution_id: str, location_id: str
    ) -> RetellSmsChatProfile:
        profile = await self.session.get(RetellSmsChatProfile, profile_id)
        if (
            profile is None
            or not profile.is_active
            or str(profile.institution_id) != institution_id
            or str(profile.location_id) != location_id
        ):
            raise RetellSmsConversationConfigurationError(
                "Retell SMS chat profile is missing, inactive, or belongs to another location"
            )
        return profile

    async def cancel_active_for_run(
        self,
        workflow_run_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        """Terminalize active local Retell sessions owned by a cancelled run.

        Local session state is authoritative. Retell may still report a lazily
        created vendor chat as ongoing, but it cannot receive another turn once
        this session is terminal and it no longer blocks a later workflow run.
        """
        sessions = list(
            (
                await self.session.execute(
                    select(RetellSmsSession)
                    .where(
                        RetellSmsSession.workflow_run_id == workflow_run_id,
                        RetellSmsSession.status.in_(ACTIVE_RETELL_SMS_SESSION_STATUSES),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if not sessions:
            return 0

        ended_at = now or datetime.now(timezone.utc)
        for retell_session in sessions:
            self.mark_terminal(
                retell_session,
                status=RetellSmsSessionStatus.CANCELLED.value,
                outcome="workflow_cancelled",
                now=ended_at,
            )
        await self.session.flush()
        return len(sessions)

    async def lock_delivery_state(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
    ) -> tuple[AutomationWorkflowRun | None, RetellSmsSession | None]:
        """Lock and refresh the run/session before an outbound reply is sent.

        The worker releases its initial claim transaction while Retell generates
        a response. A cancellation can commit during that network call, so both
        rows must be refreshed under locks before Twilio delivery. Locking the
        run first matches ``cancel_run``'s ordering and avoids a lock inversion.
        """
        run = (
            await self.session.execute(
                select(AutomationWorkflowRun)
                .where(AutomationWorkflowRun.id == workflow_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        retell_session = (
            await self.session.execute(
                select(RetellSmsSession)
                .where(RetellSmsSession.id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return run, retell_session

    async def find_active_for_inbound(
        self, inbound: InboundSmsMessage
    ) -> RetellSmsSession | None:
        if not inbound.location_id or not inbound.contact_id:
            return None
        query = select(RetellSmsSession).where(
            RetellSmsSession.institution_id == str(inbound.institution_id),
            RetellSmsSession.location_id == str(inbound.location_id),
            RetellSmsSession.contact_id == str(inbound.contact_id),
            RetellSmsSession.status.in_(ACTIVE_RETELL_SMS_SESSION_STATUSES),
        )
        if inbound.workflow_run_id:
            query = query.where(
                RetellSmsSession.workflow_run_id == str(inbound.workflow_run_id)
            )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def claim_turn(
        self,
        *,
        session_id: str,
        inbound: InboundSmsMessage,
        now: datetime | None = None,
    ) -> tuple[RetellSmsSession, RetellSmsTurn | None, bool]:
        """Lock a session and idempotently claim one inbound message.

        Returns ``(session, turn, should_process)``. A completed/failed duplicate
        has ``should_process=False`` and must never call Retell again.
        """
        now = now or datetime.now(timezone.utc)
        retell_session = (
            await self.session.execute(
                select(RetellSmsSession)
                .where(RetellSmsSession.id == session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if retell_session is None:
            raise RetellSmsConversationConfigurationError(
                "Retell SMS session not found"
            )

        existing = (
            await self.session.execute(
                select(RetellSmsTurn).where(
                    RetellSmsTurn.inbound_sms_message_id == str(inbound.id)
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return retell_session, existing, False

        in_flight = (
            await self.session.execute(
                select(RetellSmsTurn.id).where(
                    RetellSmsTurn.session_id == str(retell_session.id),
                    RetellSmsTurn.status == RetellSmsTurnStatus.CLAIMED.value,
                )
            )
        ).scalar_one_or_none()
        if in_flight is not None:
            # Retell Chat completions for one chat must be strictly ordered.
            return retell_session, None, False

        turn = RetellSmsTurn(
            institution_id=str(retell_session.institution_id),
            location_id=str(retell_session.location_id),
            session_id=str(retell_session.id),
            inbound_sms_message_id=str(inbound.id),
            message_sid=inbound.message_sid,
            status=RetellSmsTurnStatus.CLAIMED.value,
        )
        self.session.add(turn)
        retell_session.status = RetellSmsSessionStatus.GENERATING.value
        retell_session.last_activity_at = now
        await self.session.flush()
        return retell_session, turn, True

    async def dynamic_variables(
        self,
        *,
        retell_session: RetellSmsSession,
        context: dict[str, Any],
    ) -> dict[str, str]:
        contact = await self.session.get(Contact, retell_session.contact_id)
        location = await self.session.get(
            InstitutionLocation, retell_session.location_id
        )
        merge = MergeContextBuilder.build(
            contact=contact,
            location=location,
            context=context,
        )
        values = {name: merge.get(name, "") for name in AUTOMATIC_RETELL_SMS_VARIABLES}
        values.update(
            {
                "clinic_name": values.get("clinic_name", "")
                or values.get("location_name", "")
                or getattr(location, "name", ""),
                "clinic_phone": values.get("location_phone", ""),
                "clinic_timezone": getattr(location, "timezone", "") or "UTC",
                "conversation_goal": str(context.get("campaign_goal") or ""),
            }
        )
        previous = (
            await self.session.execute(
                select(SmsHistoryLog)
                .where(
                    SmsHistoryLog.conversation_thread_id
                    == str(retell_session.conversation_thread_id),
                    SmsHistoryLog.body_encrypted.is_not(None),
                )
                .order_by(SmsHistoryLog.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        values["previous_sms_message"] = (
            previous.body if previous and previous.body else ""
        )
        return {key: value[:1000] for key, value in values.items() if value != ""}

    async def finish_turn(
        self,
        *,
        retell_session: RetellSmsSession,
        turn: RetellSmsTurn,
        outbound_sms_history_id: str,
        retell_message_ids: list[str],
        now: datetime | None = None,
    ) -> bool:
        """Complete a sent turn and reset inactivity TTL; return max-turn terminal."""
        now = now or datetime.now(timezone.utc)
        turn.status = RetellSmsTurnStatus.COMPLETED.value
        turn.outbound_sms_history_id = outbound_sms_history_id
        turn.retell_message_ids = retell_message_ids
        turn.completed_at = now
        retell_session.turn_count += 1
        retell_session.last_activity_at = now
        retell_session.status = RetellSmsSessionStatus.AWAITING_USER.value
        retell_session.expires_at = min(
            now + timedelta(seconds=RETELL_SMS_POLICY.inactivity_timeout_seconds),
            retell_session.max_expires_at,
        )
        terminal = retell_session.turn_count >= RETELL_SMS_POLICY.max_patient_turns
        if terminal:
            self.mark_terminal(
                retell_session,
                status=RetellSmsSessionStatus.COMPLETED.value,
                outcome="max_turns",
                now=now,
            )
        await self.session.flush()
        return terminal

    def mark_terminal(
        self,
        retell_session: RetellSmsSession,
        *,
        status: str,
        outcome: str,
        failure_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        retell_session.status = status
        retell_session.terminal_outcome = outcome
        retell_session.failure_code = failure_code
        retell_session.ended_at = now or datetime.now(timezone.utc)

    async def create_handoff(
        self,
        retell_session: RetellSmsSession,
        *,
        reason: str,
        summary: str,
        inbound: InboundSmsMessage | None = None,
    ) -> CampaignStaffHandoff:
        existing = (
            await self.session.execute(
                select(CampaignStaffHandoff).where(
                    CampaignStaffHandoff.workflow_run_id
                    == str(retell_session.workflow_run_id),
                    CampaignStaffHandoff.conversation_thread_id
                    == str(retell_session.conversation_thread_id),
                    CampaignStaffHandoff.status.in_(("open", "assigned")),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        response_event = None
        if inbound is not None:
            source_event_id = inbound.message_sid or str(inbound.id)
            response_event = (
                await self.session.execute(
                    select(CampaignResponseEvent).where(
                        CampaignResponseEvent.institution_id
                        == str(retell_session.institution_id),
                        CampaignResponseEvent.channel == "sms",
                        CampaignResponseEvent.source_event_id == source_event_id,
                    )
                )
            ).scalar_one_or_none()
            if response_event is not None:
                response_event.normalized_intent = "staff_requested"
                response_event.normalized_outcome = "staff_handoff_required"
                response_event.summary = summary[:240]
        handoff = CampaignStaffHandoff(
            institution_id=str(retell_session.institution_id),
            location_id=str(retell_session.location_id),
            workflow_id=str(retell_session.workflow_id),
            workflow_run_id=str(retell_session.workflow_run_id),
            conversation_thread_id=str(retell_session.conversation_thread_id),
            contact_id=str(retell_session.contact_id),
            response_event_id=(str(response_event.id) if response_event else None),
            reason=reason,
            status="open",
            summary=summary[:240],
        )
        self.session.add(handoff)
        thread = await self.session.get(
            CampaignConversationThread, retell_session.conversation_thread_id
        )
        if thread is not None and thread.status != "completed":
            thread.status = "handoff"
        await self.session.flush()
        return handoff

    @staticmethod
    def result_context(retell_session: RetellSmsSession) -> dict[str, Any]:
        return {
            "retell_sms_status": retell_session.status,
            "retell_sms_outcome": retell_session.terminal_outcome,
            "retell_sms_turn_count": retell_session.turn_count,
            "retell_sms_chat_id": retell_session.retell_chat_id,
        }


def agent_response_text(
    messages: tuple[Any, ...], *, max_segments: int
) -> tuple[str, list[str]]:
    """Return bounded agent text and message ids; never forward tool/user messages."""
    agent_messages = [
        message
        for message in messages
        if str(getattr(message, "role", "")).lower() in {"agent", "assistant"}
        and str(getattr(message, "content", "")).strip()
    ]
    content = "\n".join(
        str(message.content).strip() for message in agent_messages
    ).strip()
    max_chars = 160 * max_segments
    if len(content) > max_chars:
        content = content[: max_chars - 1].rstrip() + "…"
    return content, [
        str(message.message_id) for message in agent_messages if message.message_id
    ]
