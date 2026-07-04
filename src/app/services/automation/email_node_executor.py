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
from src.app.services.messaging_credentials import TenantTwilioCredentialResolver
from src.app.services.usage_metering_service import UsageMeteringService

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
        # Send-time idempotency (XC-1): a redelivery / re-advance / quiet-hours
        # hold→resume that re-enters this node must not email the patient twice.
        if await self.runtime.already_sent(run, node.id):
            logger.info(
                "send_email idempotent skip: already sent institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

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
        email_from = TenantTwilioCredentialResolver.resolve_email_from(institution)
        from_address = email_from.from_address
        from_name = email_from.from_name

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
                # Crash-window idempotency (XC-1b): a stable per-(run, node) key so a
                # retry after a crash between send and commit is deduped by Resend
                # rather than emailing the patient twice.
                "Idempotency-Key": f"email:{run.id}:{node.id}",
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

            resend_id: str | None = None
            try:
                resend_id = (response.json() or {}).get("id")
            except Exception:  # noqa: BLE001 — body may not be JSON
                resend_id = None

        except Exception as exc:
            logger.error(
                "send_email failed: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(step, result_code="send_failed")
            await self.runtime.fail_run(run, reason=f"send_email error: {type(exc).__name__}")
            return node.next_node_id

        await self.runtime.complete_step(step, result_code="sent")

        # Meter the successful send (Plan 11). Best-effort: a metering hiccup
        # must never fail an email that already went out. Runs in this session
        # (celery/institution-scoped context is authorized for usage_events).
        # Idempotent on the Resend message id, falling back to run+node.
        try:
            await UsageMeteringService(self.session).record(
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                channel="email",
                direction="outbound",
                provider="resend",
                emails=1,
                provider_message_id=resend_id,
                idempotency_key=(
                    f"email:{resend_id}" if resend_id else f"email:{run.id}:{node.id}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — metering is best-effort
            logger.warning(
                "usage metering failed for email node=%s run=%s: %s",
                node.id, run.id, exc,
            )

        return node.next_node_id
