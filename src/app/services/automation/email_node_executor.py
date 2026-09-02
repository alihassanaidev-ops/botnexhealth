"""Executor for SendEmailNode (Plan 05).

The node addresses the enrolled patient by default, but can also target the
clinic's own staff, fixed addresses, or an address resolved from a merge field.
Consent gating and the unsubscribe footer apply to patient-directed sends only —
see ``SendEmailNode.is_patient_directed`` and ``step_dispatcher._is_patient_directed``.

Content is either inline on the node or a saved campaign template referenced by
key. The sending identity and provider are resolved per clinic
(``services.email.identity_service``), so a clinic migrated to SES sends through
SES while the rest of the platform still uses Resend.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.models.outbound_email_message import OutboundEmailMessage
from src.app.services.automation.definition_schema import SendEmailNode
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.circuit_breaker import (
    BreakerService,
    NoOpCircuitBreaker,
    ServiceBreaker,
)
from src.app.services.automation.template_renderer import build_merge_vars
from src.app.services.email.identity_service import EmailIdentityService
from src.app.services.email.reply_address import make_reply_address
from src.app.services.email.sender import (
    EmailMessage,
    EmailSender,
    EmailSendError,
    ResendSender,
    SesSender,
)
from src.app.services.staff_recipients import resolve_staff_recipients, unique_emails
from src.app.services.template_engine import render_html, render_text
from src.app.services.usage_metering_service import UsageMeteringService

logger = logging.getLogger(__name__)


def get_patient_email_sender_for(provider: str) -> EmailSender:
    """Sender for a resolved identity's provider.

    The identity decides, not global config: a clinic already migrated to SES
    keeps sending through SES even while the platform default is still Resend,
    which is what makes a per-clinic rollout possible.
    """
    return SesSender() if provider == "ses" else ResendSender()


def _mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if local and domain else "***"


# Linear backoff between send attempts. Deliberately short: the run holds a row
# lock while this executes, so a long sleep would stall the dispatcher.
_RETRY_BACKOFF_SECONDS = 2


class _RecipientError(Exception):
    """Recipient resolution failed in a way the caller reports as a step failure."""

    def __init__(self, result_code: str, reason: str) -> None:
        super().__init__(reason)
        self.result_code = result_code
        self.reason = reason


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

        # --- Resolve the sending identity (location → institution → platform) ---
        identity = await EmailIdentityService(self.session).resolve(
            str(run.institution_id),
            str(run.location_id) if run.location_id else None,
            sender_address_id=node.sender_address_id,
        )
        if not identity.is_sendable:
            return await self._abort(
                run,
                node,
                step,
                "sender_not_configured",
                "send_email: no sending address is configured for this clinic or the platform",
            )
        if identity.is_platform_fallback:
            # Not fatal — the mail still delivers — but the clinic is not sending
            # under its own identity, which is usually a misconfiguration.
            logger.info(
                "send_email using the platform sending address: institution=%s run=%s",
                run.institution_id, run.id,
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
        # Email renders through the Jinja engine so a template authored in the
        # editor behaves identically here — conditionals and filters included.
        # Text and subject render unescaped; the HTML part escapes rendered
        # patient data so a name cannot inject markup.
        merge_vars = build_merge_vars(contact, location, context)
        subject = render_text(subject_tpl, merge_vars)
        body = render_text(text_tpl, merge_vars)
        html = render_html(html_tpl, merge_vars) if html_tpl else None

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

        # --- Send through the configured provider ---
        # Always multipart when HTML exists — never HTML-only. Text-only clients,
        # screen readers and spam filters all want the plain part present.
        # Patient mail replies to a signed address that carries the conversation,
        # so an answer can be routed back to this clinic, patient and run.
        # Staff and ops mail keeps the identity's own reply-to: those recipients
        # are not in a patient conversation, and pointing them at the inbound
        # router would file colleagues' replies as patient messages.
        reply_to = identity.reply_to
        reply_domain = identity.inbound_domain or settings.ses_inbound_domain
        if patient_directed and reply_domain:
            reply_to = make_reply_address(
                reply_domain,
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                contact_id=str(run.contact_id) if run.contact_id else None,
                workflow_run_id=str(run.id),
            )

        message = EmailMessage(
            from_address=identity.from_address,
            from_name=identity.from_name,
            to=recipients,
            subject=subject,
            text=body,
            html=html,
            reply_to=reply_to,
            # Crash-window idempotency (XC-1b): Resend honors this stable
            # per-(run, node) key across the attempt loop. SES SendEmail has no
            # equivalent client token; moving patient mail to SES therefore also
            # requires the durable outbound ledger described in EMAIL_AUTOMATION.md.
            idempotency_key=f"email:{run.id}:{node.id}",
            # Scopes bounce/complaint suppression back to this institution.
            institution_id=str(run.institution_id),
            tenant_name=identity.tenant_name,
            configuration_set=identity.configuration_set,
        )
        sender = get_patient_email_sender_for(identity.provider)

        ledger: OutboundEmailMessage | None = None
        if patient_directed and contact is not None and run.location_id:
            from src.app.services.automation.campaign_conversation_service import (
                CampaignConversationService,
            )

            ledger_key = f"email:{run.id}:{node.id}"
            ledger = (
                await self.session.execute(
                    select(OutboundEmailMessage).where(
                        OutboundEmailMessage.idempotency_key == ledger_key
                    )
                )
            ).scalar_one_or_none()
            if ledger is not None and ledger.status == "sent":
                await self.runtime.complete_step(step, result_code="sent")
                return node.next_node_id
            if ledger is not None and ledger.status in ("sending", "uncertain"):
                return await self._abort(
                    run,
                    node,
                    step,
                    "send_outcome_uncertain",
                    "send_email: previous provider outcome must be reconciled",
                )

            thread = await CampaignConversationService(self.session).open_email_thread(run)
            if ledger is None:
                ledger = OutboundEmailMessage(
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id) if run.location_id else None,
                    contact_id=str(contact.id),
                    workflow_run_id=str(run.id),
                    conversation_thread_id=str(thread.id),
                    source="workflow",
                    idempotency_key=ledger_key,
                    from_address=identity.from_address,
                    sender_address_id=identity.address_id,
                    to_email_masked=_mask_email(recipients[0]),
                    status="sending",
                    attempt_count=1,
                )
                ledger.to_email = recipients[0]
                ledger.subject = subject
                ledger.body = body
                self.session.add(ledger)
            else:
                ledger.status = "sending"
                ledger.attempt_count += 1
                ledger.error_code = None
            # The reservation is durable before the provider call. A crash from
            # here leaves "sending" for reconciliation instead of resending.
            await self.session.commit()

        provider_message_id: str | None = None
        last_error: Exception | None = None
        breaker_scope = str(run.location_id or run.institution_id)
        attempts = max(1, node.max_attempts)

        for attempt in range(1, attempts + 1):
            try:
                result = await sender.send(message)
                provider_message_id = result.provider_message_id
                last_error = None
                await self.breaker.record_success(
                    BreakerService.EMAIL, breaker_scope
                )
                break

            except EmailSendError as exc:
                last_error = exc
                logger.warning(
                    "send_email attempt %d/%d failed: provider=%s institution=%s "
                    "run=%s node=%s retryable=%s error=%s",
                    attempt, attempts, sender.provider, run.institution_id,
                    run.id, node.id, exc.retryable, exc,
                )
                # A rejected address or malformed message will be rejected
                # identically next time; retrying only burns sending quota — and
                # it says nothing about the provider's health, so it must not
                # count against the breaker either.
                if not exc.retryable:
                    break
                await self.breaker.record_failure(BreakerService.EMAIL, breaker_scope)
                if attempt < attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

            except Exception as exc:  # noqa: BLE001 — reported below
                last_error = exc
                # The provider may have accepted the message before the client
                # failed. Without a provider-level idempotency guarantee, retrying
                # an unclassified transport error can email the patient twice.
                await self.breaker.record_failure(BreakerService.EMAIL, breaker_scope)
                logger.warning(
                    "send_email attempt %d/%d has uncertain outcome: institution=%s "
                    "run=%s node=%s error=%s",
                    attempt, attempts, run.institution_id, run.id, node.id, exc,
                )
                break

        if last_error is not None:
            if ledger is not None:
                ledger.status = (
                    "failed"
                    if isinstance(last_error, EmailSendError)
                    and not last_error.outcome_uncertain
                    else "uncertain"
                )
                ledger.error_code = type(last_error).__name__[:80]
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
        if ledger is not None:
            ledger.provider = sender.provider
            ledger.provider_message_id = provider_message_id
            ledger.status = "sent"
            ledger.sent_at = datetime.now(timezone.utc)

        # Meter the successful send (Plan 11). Best-effort: a metering hiccup
        # must never fail an email that already went out. Runs in this session
        # (celery/institution-scoped context is authorized for usage_events).
        # Idempotent on the provider message id, falling back to run+node.
        try:
            await UsageMeteringService(self.session).record(
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_run_id=str(run.id),
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                channel="email",
                direction="outbound",
                # Was hardcoded to "resend"; billing data would have been wrong
                # for every SES send once the provider became configurable.
                provider=sender.provider,
                emails=1,
                provider_message_id=provider_message_id,
                idempotency_key=(
                    f"email:{provider_message_id}"
                    if provider_message_id
                    else f"email:{run.id}:{node.id}"
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
