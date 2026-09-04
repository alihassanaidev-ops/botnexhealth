"""Executor for SendSmsNode — wires the automation engine to SmsService (Plan 04)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus
from src.app.services.automation.definition_schema import SendSmsNode
from src.app.services.automation.campaign_conversation_service import CampaignConversationService
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.template_renderer import render_body
from src.app.services.circuit_breaker import (
    BreakerService,
    NoOpCircuitBreaker,
    ServiceBreaker,
)
from src.app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

#: Matches the email node's ladder so both channels behave the same way.
_RETRY_BACKOFF_SECONDS = 2

# Outcome of one send attempt, decided from the provider status SmsService
# already records. Three ways, not two, for the same reason voice calls are
# classified three ways: a network timeout is genuinely ambiguous, because the
# message may have reached Twilio before the response was lost. Retrying that
# case is how you text a patient twice, so we deliberately do not.
_RETRY_SAFE = "retry_safe"
_AMBIGUOUS = "ambiguous"
_PERMANENT = "permanent"


def _classify(log: SmsHistoryLog) -> str:
    """Decide whether this failed attempt may be retried.

    ``provider_status`` is written by SmsService as ``retryable:<code>`` when
    Twilio itself rejected the request (429 or 5xx — nothing was sent, so a
    retry is safe), ``retryable:network`` when the request never completed
    (ambiguous), and ``failed:*`` for a rejection that will repeat forever.
    """
    status = (log.provider_status or "").strip().lower()
    if status == "retryable:network":
        return _AMBIGUOUS
    if status.startswith("retryable:"):
        return _RETRY_SAFE
    return _PERMANENT


class SmsNodeExecutor:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AutomationWorkflowRuntimeService,
        breaker: ServiceBreaker | None = None,
        # Accepted for the executor contract. The provider send rate is checked
        # in the dispatcher before dispatch, so nothing is enforced here.
        limits: object | None = None,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.breaker: ServiceBreaker = breaker or NoOpCircuitBreaker()

    async def execute(
        self,
        run: AutomationWorkflowRun,
        node: SendSmsNode,
        context: dict,
    ) -> str:
        """Send an SMS for this node. Returns next_node_id on success.

        On any unrecoverable failure (missing contact, no phone, no from-number,
        or Twilio error) the step and run are marked failed.
        """
        # Send-time idempotency (XC-1): a redelivery / re-advance / quiet-hours
        # hold→resume that re-enters this node must not text the patient twice.
        if await self.runtime.already_sent(run, node.id):
            logger.info(
                "send_sms idempotent skip: already sent institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)

        # --- Resolve contact ---
        if not run.contact_id:
            await self.runtime.fail_step(step, result_code="no_contact")
            await self.runtime.fail_run(run, reason="send_sms: no contact_id on run")
            return node.next_node_id

        contact: Contact | None = await self.session.get(Contact, run.contact_id)
        if contact is None:
            await self.runtime.fail_step(step, result_code="contact_not_found")
            await self.runtime.fail_run(run, reason=f"send_sms: contact {run.contact_id} not found")
            return node.next_node_id

        to_number = contact.phone
        if not to_number:
            await self.runtime.fail_step(step, result_code="no_phone")
            await self.runtime.fail_run(run, reason="send_sms: contact has no phone number")
            return node.next_node_id

        # --- Resolve location + from-number ---
        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        from_number = location.twilio_from_number if location else None
        if not from_number:
            await self.runtime.fail_step(step, result_code="no_from_number")
            await self.runtime.fail_run(run, reason="send_sms: location has no twilio_from_number")
            return node.next_node_id

        thread = await CampaignConversationService(self.session).open_sms_thread(run)

        # --- Render body ---
        # Fail closed: a token that resolves to nothing leaves a hole in the
        # message ("Time for your  visit"). Publish-time validation catches most
        # of these, but a value can still be absent for one particular patient,
        # and delivering the gap is worse than skipping the send. The run
        # continues so later steps still get their chance.
        rendered = render_body(node.body_template, contact, location, context)
        if not rendered.complete:
            logger.warning(
                "send_sms: skipping run=%s node=%s, unresolved merge fields %s",
                run.id,
                node.id,
                rendered.unresolved,
            )
            await self.runtime.complete_step(
                step,
                result_code="skipped_incomplete_merge",
                result_metadata={"unresolved_fields": rendered.unresolved},
            )
            return node.next_node_id
        body = rendered.text

        # --- Send ---
        # SmsService does not raise on a provider failure: it records the
        # outcome on the history row and returns it. Ignoring that return value
        # is what let a failed send be recorded as a delivered one.
        sms_service = SmsService(self.session)
        breaker_scope = str(run.location_id or run.institution_id)
        attempts = max(1, node.max_attempts)
        outcome = _PERMANENT

        for attempt in range(1, attempts + 1):
            try:
                sms_log = await sms_service.send_sms(
                    from_number=from_number,
                    to_number=to_number,
                    body=body,
                    institution_location_id=str(run.location_id),
                    patient_contact_id=str(run.contact_id),
                    workflow_run_id=str(run.id),
                    workflow_id=str(run.workflow_id),
                    conversation_thread_id=str(thread.id),
                    include_opt_out_footer=node.include_opt_out_footer,
                )
            except Exception as exc:
                # Configuration problems only — SmsService raises before it
                # reaches Twilio, so nothing was sent and nothing is ambiguous.
                logger.error(
                    "send_sms failed: institution=%s run=%s node=%s error=%s",
                    run.institution_id, run.id, node.id, exc,
                )
                await self.runtime.fail_step(step, result_code="send_failed")
                await self.runtime.fail_run(
                    run, reason=f"send_sms error: {type(exc).__name__}"
                )
                return node.next_node_id

            if sms_log.status != SmsStatus.FAILED.value:
                await self.breaker.record_success(
                    BreakerService.TWILIO, breaker_scope
                )
                await CampaignConversationService(self.session).mark_message_seen(thread)
                # Carry the provider's message id so the delivery receipt, which
                # arrives minutes later on a webhook, can find this attempt.
                await self.runtime.complete_step(
                    step,
                    result_code="sent",
                    result_metadata=(
                        {"message_sid": sms_log.message_sid}
                        if sms_log.message_sid
                        else None
                    ),
                )
                return node.next_node_id

            outcome = _classify(sms_log)
            if outcome is not _PERMANENT:
                # Twilio rejected us (429/5xx) or the request never completed.
                # A _PERMANENT rejection is this message being wrong — a bad
                # number, an unreachable carrier — and says nothing about
                # Twilio's health, so it must not trip the breaker.
                await self.breaker.record_failure(
                    BreakerService.TWILIO, breaker_scope
                )
            logger.warning(
                "send_sms attempt %d/%d failed: institution=%s run=%s node=%s "
                "provider_status=%s outcome=%s",
                attempt, attempts, run.institution_id, run.id, node.id,
                sms_log.provider_status, outcome,
            )
            if outcome is not _RETRY_SAFE:
                break
            if attempt < attempts:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        result_code = {
            _AMBIGUOUS: "send_failed_ambiguous",
            _PERMANENT: "send_failed_permanent",
        }.get(outcome, "send_failed_retries_exhausted")

        await self.runtime.fail_step(step, result_code=result_code)
        await self.runtime.fail_run(run, reason=f"send_sms: {result_code}")
        return node.next_node_id
