"""Terminate SMS-correlated workflow runs when a patient opts out by text."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowRun,
)
from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.contact import Contact
from src.app.services.automation.enrollment_service import (
    AutomationWorkflowEnrollmentService,
)
from src.app.services.automation.scheduler_service import (
    AutomationWorkflowSchedulerService,
)
from src.app.services.sms_privacy import hash_phone

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = (
    AutomationRunStatus.PENDING.value,
    AutomationRunStatus.RUNNING.value,
    AutomationRunStatus.WAITING.value,
)
_ACTIVE_SMS_THREAD_STATUSES = ("open", "handoff")


class SmsOptOutWorkflowService:
    """Cancel active runs that own the SMS conversation receiving STOP.

    The caller owns the transaction. This service deliberately never commits,
    allowing suppression creation, timer cancellation, run cancellation, and
    thread closure to succeed or roll back as one unit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cancel_active_sms_runs(
        self,
        *,
        institution_id: str,
        location_id: str,
        phone: str | None,
        correlated_run_id: str | None,
    ) -> int:
        """Cancel the exact correlated run, or every matching ambiguous SMS run.

        When inbound routing identifies one reply-eligible thread, its run ID is
        authoritative. If a shared phone makes routing ambiguous, the fallback
        remains constrained to active runs with active SMS threads at the same
        institution and location. Email/voice-only runs cannot match the join.
        """
        stmt = (
            select(AutomationWorkflowRun)
            .join(
                CampaignConversationThread,
                CampaignConversationThread.workflow_run_id == AutomationWorkflowRun.id,
            )
            .where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.location_id == location_id,
                AutomationWorkflowRun.status.in_(_ACTIVE_RUN_STATUSES),
                CampaignConversationThread.institution_id == institution_id,
                CampaignConversationThread.location_id == location_id,
                CampaignConversationThread.channel == "sms",
                CampaignConversationThread.status.in_(_ACTIVE_SMS_THREAD_STATUSES),
            )
        )

        if correlated_run_id:
            stmt = stmt.where(AutomationWorkflowRun.id == correlated_run_id)
        else:
            phone_hash = hash_phone(phone)
            if not phone_hash:
                return 0
            stmt = stmt.join(
                Contact,
                Contact.id == CampaignConversationThread.contact_id,
            ).where(
                Contact.institution_id == institution_id,
                Contact.phone_hash == phone_hash,
            )

        runs = list((await self.session.execute(stmt)).scalars().all())
        if not runs:
            return 0

        scheduler = AutomationWorkflowSchedulerService(self.session)
        enrollment = AutomationWorkflowEnrollmentService(self.session)
        for run in runs:
            await scheduler.cancel_timers_for_run(str(run.id))
            await enrollment.cancel_run(
                run,
                reason="sms_opt_out",
                sms_completion_reason="sms_opt_out",
                preserve_unresolved_sms_handoffs=False,
                require_sms_thread_close=True,
            )

        logger.info(
            "sms opt-out: cancelled %d active SMS workflow run(s) institution=%s location=%s correlated=%s",
            len(runs),
            institution_id,
            location_id,
            bool(correlated_run_id),
        )
        return len(runs)
