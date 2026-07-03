"""Executor for SendEmailNode — sends plain-text campaign emails via Resend (Plan 05)."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import SendEmailNode
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.template_renderer import render_sms_body

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


def _build_from(address: str, name: str | None) -> str:
    """Return 'Name <address>' or just 'address' for the Resend from field."""
    if name:
        return f"{name} <{address}>"
    return address


class EmailNodeExecutor:
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
        node: SendEmailNode,
        context: dict,
    ) -> str:
        """Send a plain-text email for this node. Returns next_node_id on success."""
        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)

        # --- Resolve contact email ---
        if not run.contact_id:
            await self.runtime.fail_step(step, result_code="no_contact")
            await self.runtime.fail_run(run, reason="send_email: no contact_id on run")
            return node.next_node_id

        contact: Contact | None = await self.session.get(Contact, run.contact_id)
        if contact is None:
            await self.runtime.fail_step(step, result_code="contact_not_found")
            await self.runtime.fail_run(run, reason=f"send_email: contact {run.contact_id} not found")
            return node.next_node_id

        to_email = contact.email
        if not to_email:
            await self.runtime.fail_step(step, result_code="no_email")
            await self.runtime.fail_run(run, reason="send_email: contact has no email address")
            return node.next_node_id

        # --- Resolve from-address (institution → platform fallback) ---
        institution: Institution | None = await self.session.get(Institution, run.institution_id)
        from_address = (
            (institution.email_from_address if institution else None)
            or settings.resend_from_email
        )
        from_name = institution.email_from_name if institution else None

        api_key = settings.resend_api_key
        if not api_key or not from_address:
            await self.runtime.fail_step(step, result_code="resend_not_configured")
            await self.runtime.fail_run(run, reason="send_email: Resend not configured (RESEND_API_KEY / from address)")
            return node.next_node_id

        # --- Resolve location for template merge vars ---
        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )

        # --- Render templates ---
        subject = render_sms_body(node.subject_template, contact, location, context)
        body = render_sms_body(node.body_template, contact, location, context)

        # --- Send via Resend ---
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "from": _build_from(from_address, from_name),
                "to": [to_email],
                "subject": subject,
                "text": body,
            }
            if settings.resend_reply_to:
                payload["reply_to"] = settings.resend_reply_to

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(_RESEND_URL, headers=headers, json=payload)

            if response.status_code >= 400:
                raise RuntimeError(
                    f"Resend returned {response.status_code}: {response.text[:200]}"
                )

        except Exception as exc:
            logger.error(
                "send_email failed: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(step, result_code="send_failed")
            await self.runtime.fail_run(run, reason=f"send_email error: {type(exc).__name__}")
            return node.next_node_id

        await self.runtime.complete_step(step, result_code="sent")
        return node.next_node_id
