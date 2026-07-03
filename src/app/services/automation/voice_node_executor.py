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
        payload = {
            "from_number": from_number,
            "to_number": to_number,
            "override_agent_id": node.retell_agent_id,
            "retell_llm_dynamic_variables": {
                "first_name": first_name,
                "user_number": to_number,
            },
            "metadata": {
                "workflow_run_id": str(run.id),
                "institution_id": str(run.institution_id),
                "source": "outbound_campaign",
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

        await self.runtime.complete_step(step, result_code="call_placed")
        return node.next_node_id
