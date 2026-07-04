"""Executor for SendVoiceNode — places outbound campaign calls via Retell (Plan 03).

Places a per-location outbound AI call as a workflow action. The vendor HTTP call
is delegated to the mockable ``RetellOutboundClient`` (error classification lives
there). This executor is the orchestrator: idempotency guard, resolve
contact/location/creds, place the call, capture the ``retell_call_id`` onto the
attempt (so the post-call webhook can correlate the outcome back to this run), and
apply retry/give-up semantics.

Outcome handling: when ``node.wait_for_outcome`` is set, a successful placement
returns a ``VoiceParked`` signal so the dispatcher parks the run WAITING until the
Retell post-call webhook resumes it with the dial outcome. Otherwise the node is
fire-and-forget (advances immediately).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import SendVoiceNode
from src.app.services.automation.retell_outbound_client import (
    RetellOutboundClient,
    RetellPermanentError,
    RetellTransientError,
)
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService

logger = logging.getLogger(__name__)

_CALL_PLACED = "call_placed"
# A placed call that is parked WAITING for its post-call outcome webhook. Distinct
# from a completed fire-and-forget "call_placed" so resume knows the call already
# went out (advance past, never re-dial).
_CALL_PLACED_AWAITING = "call_placed_awaiting_outcome"


@dataclass(frozen=True)
class VoiceParked:
    """Signal to the dispatcher: the call was placed and the run should PARK
    WAITING for the outcome webhook. Carries the parked step + safety-timeout."""

    step: object  # AutomationWorkflowStepExecution (the WAITING step to wait_run + timer)
    timeout_minutes: int = 30


def _ai_call_disclosure(clinic_name: str | None) -> str:
    """Spoken AI-call identity disclosure + opt-out (TCPA artificial-voice / CASL).
    Passed to Retell as a dynamic variable so the agent prompt opens every outbound
    call by identifying the clinic, stating the call is automated, and offering an
    opt-out. Spoken delivery lives in the Retell agent prompt (must reference
    ``{{compliance_disclosure}}``); this is the authoritative text the engine supplies."""
    clinic = (clinic_name or "your dental clinic").strip() or "your dental clinic"
    return (
        f"This is an automated call from {clinic}. "
        "If you would prefer not to receive automated calls, say 'stop' at any "
        "time and we will not call you again."
    )


class VoiceNodeExecutor:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AutomationWorkflowRuntimeService,
    ) -> None:
        self.session = session
        self.runtime = runtime

    async def execute(
        self,
        run: AutomationWorkflowRun,
        node: SendVoiceNode,
        context: dict,
    ) -> str | VoiceParked:
        """Place an outbound call. Returns next_node_id (fire-and-forget) or a
        VoiceParked signal (wait-for-outcome). On unrecoverable failure the step and
        run are failed; on a transient Retell error the exception is re-raised so the
        Celery task retries (until node.max_attempts is exhausted)."""
        # Send-time idempotency (XC-1): a redelivery / re-advance / hold-resume that
        # re-enters this node must not dial the patient again.
        if await self.runtime.already_sent(run, node.id):
            logger.info(
                "send_voice idempotent skip: call already placed institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)

        # --- Resolve contact / phone / location / creds (all permanent failures) ---
        if not run.contact_id:
            await self.runtime.fail_step(step, result_code="no_contact")
            await self.runtime.fail_run(run, reason="send_voice: no contact_id on run")
            return node.next_node_id

        contact: Contact | None = await self.session.get(Contact, run.contact_id)
        if contact is None:
            await self.runtime.fail_step(step, result_code="contact_not_found")
            await self.runtime.fail_run(run, reason=f"send_voice: contact {run.contact_id} not found")
            return node.next_node_id

        to_number = contact.phone
        if not to_number:
            await self.runtime.fail_step(step, result_code="no_phone")
            await self.runtime.fail_run(run, reason="send_voice: contact has no phone number")
            return node.next_node_id

        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        from_number = location.retell_from_number if location else None
        if not from_number:
            await self.runtime.fail_step(step, result_code="no_from_number")
            await self.runtime.fail_run(run, reason="send_voice: location has no retell_from_number")
            return node.next_node_id

        api_key = settings.retell_api_secret
        if not api_key:
            await self.runtime.fail_step(step, result_code="retell_not_configured")
            await self.runtime.fail_run(run, reason="send_voice: Retell not configured (RETELL_API_SECRET)")
            return node.next_node_id

        first_name = (contact.first_name or "").strip()
        clinic_name = getattr(location, "name", None)
        dynamic_variables = {
            "first_name": first_name,
            "user_number": to_number,
            "clinic_name": clinic_name or "",
            "compliance_disclosure": _ai_call_disclosure(clinic_name),
        }
        metadata = {
            "workflow_run_id": str(run.id),
            "workflow_step_id": node.id,
            "institution_id": str(run.institution_id),
            "source": "outbound_campaign",
            "ai_automated_call": True,
        }

        # --- Place the call via the mockable client ---
        try:
            result = await RetellOutboundClient(api_key).create_phone_call(
                from_number=from_number,
                to_number=to_number,
                override_agent_id=node.retell_agent_id,
                dynamic_variables=dynamic_variables,
                metadata=metadata,
            )
        except RetellTransientError as exc:
            # Recoverable vendor blip. Retry via the Celery task until max_attempts
            # is exhausted, then give up (fail the run rather than loop forever).
            await self.runtime.fail_step(step, result_code="retrying_transient", error_message=str(exc))
            if step.attempt_number >= node.max_attempts:
                logger.error(
                    "send_voice transient error, attempts exhausted (%d): run=%s node=%s err=%s",
                    node.max_attempts, run.id, node.id, exc,
                )
                await self.runtime.fail_run(run, reason="send_voice: transient error, attempts exhausted")
                return node.next_node_id
            logger.warning(
                "send_voice transient error (attempt %d/%d), re-raising for retry: run=%s node=%s err=%s",
                step.attempt_number, node.max_attempts, run.id, node.id, exc,
            )
            raise  # propagate → Celery task retries with backoff
        except (RetellPermanentError, Exception) as exc:  # noqa: BLE001
            logger.error(
                "send_voice permanent failure: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(step, result_code="send_failed", error_message=str(exc))
            await self.runtime.fail_run(run, reason=f"send_voice error: {type(exc).__name__}")
            return node.next_node_id

        # --- Placed successfully: store retell_call_id for webhook correlation ---
        if node.wait_for_outcome:
            # Park WAITING for the outcome webhook. Keep the step WAITING with the
            # placed-call marker + retell_call_id so resume advances past (never re-dials).
            await self.runtime.mark_step_awaiting_outcome(
                step,
                result_code=_CALL_PLACED_AWAITING,
                result_metadata={"retell_call_id": result.call_id},
            )
            return VoiceParked(step=step)

        await self.runtime.complete_step(
            step, result_code=_CALL_PLACED, result_metadata={"retell_call_id": result.call_id}
        )
        return node.next_node_id
