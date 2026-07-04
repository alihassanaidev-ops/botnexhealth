"""Executor for SendVoiceNode — places outbound campaign calls via Retell (Plan 03).

Fire-and-forget (v1): places the call and advances immediately. The call's
outcome is recorded by the existing Retell webhook (which already handles
direction=="outbound"), not by this executor — so we never write the Call row
here. `run.id` is stamped into the call metadata so a future wait-for-outcome
model can correlate the call back to this run without a backfill.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import SendVoiceNode
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService

logger = logging.getLogger(__name__)

_CREATE_CALL_URL = "https://api.retellai.com/v2/create-phone-call"
_CALL_PLACED = "call_placed"


def _ai_call_disclosure(clinic_name: str | None) -> str:
    """Spoken AI-call identity disclosure + opt-out (TCPA artificial-voice /
    CASL). Passed to Retell as a dynamic variable so the agent prompt opens
    every outbound call by identifying the clinic, stating the call is
    automated, and offering an opt-out — the compliance obligation for
    AI/artificial-voice outreach (Plan 12). The spoken delivery lives in the
    Retell agent prompt (which must reference ``{{compliance_disclosure}}``);
    this is the authoritative text the engine supplies."""
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
    ) -> str:
        """Place an outbound call for this node. Returns next_node_id on success.

        On any unrecoverable failure (missing contact, no phone, no from-number,
        Retell not configured, or a Retell API error) the step and run are failed.
        """
        # Send-time idempotency (XC-1): placing an outbound call is not naturally
        # idempotent, so a timer redelivery, a re-advance, or a quiet-hours
        # hold→resume that re-enters this node must NOT dial the patient again.
        # Shared guard keyed on a completed send step for this (run, node).
        if await self.runtime.already_sent(run, node.id):
            logger.info(
                "send_voice idempotent skip: call already placed institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)

        # --- Resolve contact ---
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

        # --- Resolve location + Retell from-number ---
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

        # --- Place the call ---
        # Dynamic variables mirror the agent prompt's expected vars (first_name,
        # user_number). metadata.workflow_run_id is the correlation hedge.
        first_name = (contact.first_name or "").strip()
        clinic_name = getattr(location, "name", None)
        payload = {
            "from_number": from_number,
            "to_number": to_number,
            "override_agent_id": node.retell_agent_id,
            "retell_llm_dynamic_variables": {
                "first_name": first_name,
                "user_number": to_number,
                # Compliance disclosure the agent prompt must speak at call open
                # (AI-call identity + opt-out). See _ai_call_disclosure.
                "clinic_name": clinic_name or "",
                "compliance_disclosure": _ai_call_disclosure(clinic_name),
            },
            "metadata": {
                "workflow_run_id": str(run.id),
                "institution_id": str(run.institution_id),
                "source": "outbound_campaign",
                # Marks this as an automated/artificial-voice call for downstream
                # audit and post-call classification.
                "ai_automated_call": True,
            },
        }

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(_CREATE_CALL_URL, headers=headers, json=payload)

            if response.status_code >= 400:
                raise RuntimeError(
                    f"Retell returned {response.status_code}: {response.text[:200]}"
                )

        except Exception as exc:
            logger.error(
                "send_voice failed: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(step, result_code="send_failed")
            await self.runtime.fail_run(run, reason=f"send_voice error: {type(exc).__name__}")
            return node.next_node_id

        await self.runtime.complete_step(step, result_code=_CALL_PLACED)
        return node.next_node_id
