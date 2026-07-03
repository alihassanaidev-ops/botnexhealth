"""Executor for SendSmsNode — wires the automation engine to SmsService (Plan 04)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import SendSmsNode
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.template_renderer import render_sms_body
from src.app.services.sms_service import SmsService

logger = logging.getLogger(__name__)


class SmsNodeExecutor:
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
        node: SendSmsNode,
        context: dict,
    ) -> str:
        """Send an SMS for this node. Returns next_node_id on success.

        On any unrecoverable failure (missing contact, no phone, no from-number,
        or Twilio error) the step and run are marked failed.
        """
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

        # --- Render body ---
        body = render_sms_body(node.body_template, contact, location, context)

        # --- Send ---
        try:
            sms_service = SmsService(self.session)
            await sms_service.send_sms(
                from_number=from_number,
                to_number=to_number,
                body=body,
                institution_location_id=str(run.location_id),
                patient_contact_id=str(run.contact_id),
            )
        except Exception as exc:
            logger.error(
                "send_sms failed: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(step, result_code="send_failed")
            await self.runtime.fail_run(run, reason=f"send_sms error: {type(exc).__name__}")
            return node.next_node_id

        await self.runtime.complete_step(step, result_code="sent")
        return node.next_node_id
