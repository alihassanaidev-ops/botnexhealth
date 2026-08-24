"""Executor for SendEmailNode — sends plain-text campaign emails via Resend (Plan 05).

The node addresses the enrolled patient by default, but can also target the
clinic's own staff, fixed addresses, or an address resolved from a merge field.
Consent gating and the unsubscribe footer apply to patient-directed sends only —
see ``SendEmailNode.is_patient_directed`` and ``step_dispatcher._is_patient_directed``.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape

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
from src.app.services.staff_recipients import resolve_staff_recipients, unique_emails
from src.app.services.usage_metering_service import UsageMeteringService

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
# Linear backoff between send attempts. Deliberately short: the run holds a row
# lock while this executes, so a long sleep would stall the dispatcher.
_RETRY_BACKOFF_SECONDS = 2


class _RecipientError(Exception):
    """Recipient resolution failed in a way the caller reports as a step failure."""

    def __init__(self, result_code: str, reason: str) -> None:
        super().__init__(reason)
        self.result_code = result_code
        self.reason = reason


def _build_from(address: str, name: str | None) -> str:
    """Return 'Name <address>' or just 'address' for the Resend from field."""
    if name:
        return f"{name} <{address}>"
    return address


def _unsubscribe_footer_html(url: str, clinic_name: str | None) -> str:
    """HTML counterpart of ``unsubscribe_footer``.

    The plain-text footer is appended to the text part; an HTML email needs its
    own or the one-click unsubscribe is invisible to anyone reading the HTML
    version — which is nearly everyone.
    """
    who = escape(clinic_name or "this clinic")
    safe_url = escape(url, quote=True)
    return (
        '<hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb">'
        '<p style="margin:0;font-size:12px;line-height:18px;color:#6b7280;">'
        f"You're receiving this because you're a patient of {who}. "
        f'<a href="{safe_url}" style="color:#6b7280;">Unsubscribe</a>.'
        "</p>"
    )


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

        # --- Resolve the contact (needed for merge fields even when the email
        # is addressed elsewhere; a staff alert still renders patient details) ---
        contact: Contact | None = None
        if run.contact_id:
            contact = await self.session.get(Contact, run.contact_id)

        patient_directed = node.is_patient_directed
        if patient_directed:
            if not run.contact_id:
                return await self._abort(
                    run, node, step, "no_contact", "send_email: no contact_id on run"
                )
            if contact is None:
                return await self._abort(
                    run,
                    node,
                    step,
                    "contact_not_found",
                    f"send_email: contact {run.contact_id} not found",
                )

        try:
            recipients = await self._resolve_recipients(run, node, contact)
        except _RecipientError as exc:
            return await self._abort(run, node, step, exc.result_code, exc.reason)

        if not recipients:
            return await self._abort(
                run, node, step, "no_email", "send_email: no recipient address resolved"
            )

        # --- Resolve from-address (institution → platform fallback) ---
        institution: Institution | None = await self.session.get(Institution, run.institution_id)
        email_from = TenantTwilioCredentialResolver.resolve_email_from(institution)
        from_address = email_from.from_address
        from_name = email_from.from_name

        api_key = settings.resend_api_key
        if not api_key or not from_address:
            return await self._abort(
                run,
                node,
                step,
                "resend_not_configured",
                "send_email: Resend not configured (RESEND_API_KEY / from address)",
            )

        # --- Resolve location for template merge vars ---
        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )

        # --- Resolve content: a saved template, or the node's inline text ---
        subject_tpl = node.subject_template
        text_tpl = node.body_template
        html_tpl = node.html_template

        if node.template_key:
            from src.app.services.campaign_email_template_service import (
                CampaignEmailTemplateService,
            )

            saved = await CampaignEmailTemplateService(self.session).get_by_key(
                str(run.institution_id), node.template_key
            )
            if saved is None or not saved.is_active:
                # Publish-time validation rejects this, so reaching it means the
                # template was deleted or deactivated after the workflow went
                # live. Fail loudly rather than sending an empty email.
                return await self._abort(
                    run,
                    node,
                    step,
                    "template_unavailable",
                    f"send_email: template '{node.template_key}' is missing or inactive",
                )
            subject_tpl = saved.subject_template
            text_tpl = saved.text_body
            html_tpl = saved.html_body

        # --- Render templates ---
        subject = render_sms_body(subject_tpl, contact, location, context)
        body = render_sms_body(text_tpl, contact, location, context)
        html = render_sms_body(html_tpl, contact, location, context) if html_tpl else None

        # --- Append the one-click unsubscribe footer (CAN-SPAM/CASL, Plan 05) ---
        # Patient-directed sends only. On a staff alert the footer is not merely
        # noise: clicking it would write a *patient* consent revocation keyed on
        # the staff member's address.
        if patient_directed:
            from src.app.services.email_unsubscribe import (
                make_unsubscribe_token,
                unsubscribe_footer,
                unsubscribe_url,
            )
            from src.app.services.sms_privacy import hash_email

            _email_hash = hash_email(recipients[0])
            if _email_hash:
                _token = make_unsubscribe_token(str(run.institution_id), _email_hash)
                _url = unsubscribe_url(settings.public_base_url, _token)
                _clinic = getattr(location, "name", None)
                body = body + unsubscribe_footer(_url, _clinic)
                if html:
                    html = html + _unsubscribe_footer_html(_url, _clinic)

        # --- Send via Resend ---
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Crash-window idempotency (XC-1b): a stable per-(run, node) key so a
            # retry after a crash between send and commit is deduped by Resend
            # rather than emailing the patient twice. The key is deliberately
            # stable across the attempt loop below for the same reason.
            "Idempotency-Key": f"email:{run.id}:{node.id}",
        }
        payload = {
            "from": _build_from(from_address, from_name),
            "to": recipients,
            "subject": subject,
            "text": body,
            # Tag so the Resend bounce/complaint webhook can scope suppression
            # back to this institution (Plan 05).
            "tags": [{"name": "institution_id", "value": str(run.institution_id)}],
        }
        # Always multipart when HTML exists — never HTML-only. Text-only clients,
        # screen readers and spam filters all want the plain part present.
        if html:
            payload["html"] = html
        if settings.resend_reply_to:
            payload["reply_to"] = settings.resend_reply_to

        resend_id: str | None = None
        last_error: Exception | None = None
        attempts = max(1, node.max_attempts)

        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(_RESEND_URL, headers=headers, json=payload)

                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Resend returned {response.status_code}: {response.text[:200]}"
                    )

                try:
                    resend_id = (response.json() or {}).get("id")
                except Exception:  # noqa: BLE001 — body may not be JSON
                    resend_id = None

                last_error = None
                break

            except Exception as exc:  # noqa: BLE001 — retried, then reported below
                last_error = exc
                logger.warning(
                    "send_email attempt %d/%d failed: institution=%s run=%s node=%s error=%s",
                    attempt, attempts, run.institution_id, run.id, node.id, exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        if last_error is not None:
            logger.error(
                "send_email failed after %d attempt(s): institution=%s run=%s node=%s error=%s",
                attempts, run.institution_id, run.id, node.id, last_error,
            )
            return await self._abort(
                run,
                node,
                step,
                "send_failed",
                f"send_email error: {type(last_error).__name__}",
            )

        await self.runtime.complete_step(step, result_code="sent")

        # Meter the successful send (Plan 11). Best-effort: a metering hiccup
        # must never fail an email that already went out. Runs in this session
        # (celery/institution-scoped context is authorized for usage_events).
        # Idempotent on the Resend message id, falling back to run+node.
        try:
            await UsageMeteringService(self.session).record(
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_run_id=str(run.id),
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _abort(
        self,
        run: AutomationWorkflowRun,
        node: SendEmailNode,
        step,  # AutomationWorkflowStepExecution
        result_code: str,
        reason: str,
    ) -> str:
        """Fail the step, then either fail the run or carry on down the branch.

        ``on_failure="continue"`` exists because an optional courtesy email
        should not abandon an appointment workflow that has already done its
        real work. The step is still recorded as failed either way, so the
        failure stays visible in campaign reporting.
        """
        await self.runtime.fail_step(step, result_code=result_code)
        if node.on_failure == "continue":
            logger.warning(
                "send_email %s — continuing (on_failure=continue): run=%s node=%s",
                result_code, run.id, node.id,
            )
        else:
            await self.runtime.fail_run(run, reason=reason)
        return node.next_node_id

    async def _resolve_recipients(
        self,
        run: AutomationWorkflowRun,
        node: SendEmailNode,
        contact: Contact | None,
    ) -> list[str]:
        """Resolve the address list for this node's configured recipient."""
        recipient = node.recipient
        kind = recipient.kind

        if kind == "contact":
            email = (contact.email or "").strip() if contact else ""
            if not email:
                raise _RecipientError(
                    "no_email", "send_email: contact has no email address"
                )
            return [email]

        if kind == "staff":
            emails = await resolve_staff_recipients(
                self.session,
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                notification_type=recipient.notification_type,
                include_external=recipient.include_external,
            )
            if not emails:
                raise _RecipientError(
                    "no_staff_recipients",
                    "send_email: no active staff recipients for this institution/location",
                )
            return emails

        if kind == "static":
            return unique_emails(list(recipient.addresses))

        if kind == "merge_field":
            from src.app.services.automation.merge_field_catalog import (
                MergeContextBuilder,
            )

            location = (
                await self.session.get(InstitutionLocation, run.location_id)
                if run.location_id
                else None
            )
            merge_vars = MergeContextBuilder.build(
                contact=contact,
                location=location,
                context=MergeContextBuilder.normalize_raw_context(run.context or {}),
            )
            email = (merge_vars.get(recipient.field) or "").strip()
            if not email:
                raise _RecipientError(
                    "no_email",
                    f"send_email: merge field '{recipient.field}' resolved to no address",
                )
            return [email]

        raise _RecipientError(
            "unsupported_recipient", f"send_email: unsupported recipient kind '{kind}'"
        )
