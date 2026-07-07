"""Real ComplianceGate implementation for Plan 12.

Replaces NoOpComplianceGate in the step dispatcher. Three sequential checks:
  1. Emergency halt   — institution-level kill switch (OutboundEmergencyHalt)
  2. Quiet hours      — location send window via QuietHoursService (hold, not block)
  3. Channel consent  — SMS via SmsComplianceService; email/voice via ConsentRecord

Hold semantics: a hold defers the send to the next permitted window
(``GateResult.retry_at``); the dispatcher schedules a timer for that time and the
run resumes and re-checks the gate then — it is never dropped. If no permitted
window exists within the horizon the gate returns "block" (no_permitted_window).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.outbound_halt import OutboundEmergencyHalt
from src.app.models.sms_consent import ConsentBasis, ConsentChannel, ConsentRecord, ConsentStatus
from src.app.services.automation.compliance_gate import ComplianceGate, GateResult
from src.app.services.automation.quiet_hours_service import QuietHoursService
from src.app.services.sms_compliance import SmsSendBlockedError, SmsComplianceService
from src.app.services.sms_privacy import hash_email, hash_phone

logger = logging.getLogger(__name__)

# Consent-basis matrix by content class (TCPA/CASL; A-5 signed off 2026-07-04).
# Marketing/sales AI outreach requires express written consent; recall requires at
# least express; care reminders (transactional_care / unset) accept any basis.
_MARKETING_CONTENT_CLASSES = {"sales", "marketing"}
_ALL_BASES = frozenset(b.value for b in ConsentBasis)

# Content classes that carry IMPLIED consent when the patient's channel identifier
# is already on file (Option B, 2026-07-07). A patient who gave the clinic their
# email/phone for care is treated as consenting to TRANSACTIONAL / appointment
# messages, so those don't require an explicit consent record. Recall and
# marketing/sales are excluded — they still need an express recorded consent.
# (Opt-outs — DNC / revoked consent — are enforced *before* this check, so an
# opted-out patient is never reached by the implied path.)
_IMPLIED_CONSENT_CLASSES = {None, "transactional_care"}


def _acceptable_bases(content_class: str | None) -> frozenset[str]:
    if content_class in _MARKETING_CONTENT_CLASSES:
        return frozenset({ConsentBasis.EXPRESS_WRITTEN.value})
    if content_class == "recall":
        return frozenset({ConsentBasis.EXPRESS_WRITTEN.value, ConsentBasis.EXPRESS.value})
    return _ALL_BASES


class ComplianceGateService:
    """Production compliance gate. Implements the ComplianceGate protocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(
        self,
        run: AutomationWorkflowRun,
        channel_type: str,
        *,
        now: datetime | None = None,
        content_class: str | None = None,
    ) -> GateResult:
        """Run all compliance checks in priority order.

        ``content_class`` (from the workflow's ComplianceMetadata) selects the
        required consent basis: marketing/sales require an express(_written) basis,
        recall requires express, care reminders accept implied/exempt (V-3)."""
        # 1. Emergency halt — blocks everything for the institution
        if await self._active_halt(run.institution_id):
            logger.info(
                "compliance gate: blocked by emergency halt institution=%s run=%s",
                run.institution_id, run.id,
            )
            return GateResult(action="block", reason="emergency_halt")

        # 2. Quiet hours — defer to the next permitted send window (never drop)
        if run.location_id:
            quiet = QuietHoursService(self.session)
            if await quiet.is_quiet_hours(run.location_id, now=now):
                retry_at = await quiet.next_permitted_window(run.location_id, now=now)
                if retry_at is None:
                    logger.info(
                        "compliance gate: block no_permitted_window location=%s run=%s",
                        run.location_id, run.id,
                    )
                    return GateResult(action="block", reason="no_permitted_window")
                logger.info(
                    "compliance gate: hold quiet_hours location=%s run=%s retry_at=%s",
                    run.location_id, run.id, retry_at,
                )
                return GateResult(action="hold", reason="quiet_hours", retry_at=retry_at)

        # 3. Contact required for consent checks
        if run.contact_id is None:
            logger.warning(
                "compliance gate: blocked no_contact institution=%s run=%s",
                run.institution_id, run.id,
            )
            return GateResult(action="block", reason="no_contact")

        contact = await self.session.get(Contact, run.contact_id)
        if contact is None:
            return GateResult(action="block", reason="contact_not_found")

        if channel_type == "send_sms":
            # assert_can_send enforces DNC (scope-aware) + suppression + consent.
            return await self._check_sms(run, contact)

        # Do-not-contact applies to EVERY channel (scope §11). The SMS path
        # enforces it inside assert_can_send; enforce it here for voice/email so
        # a "remove me everywhere" patient is never voice-called or emailed.
        if channel_type in ("send_email", "send_voice"):
            phone_hash = hash_phone(contact.phone) if contact.phone else None
            if await SmsComplianceService(self.session).is_do_not_contact(
                institution_id=run.institution_id,
                location_id=run.location_id,
                phone_hash=phone_hash,
                contact_id=run.contact_id,
            ):
                return GateResult(action="block", reason="do_not_contact")

        # Email consent is keyed on the email address, not the phone — an
        # email-only contact must not be blocked "no_phone".
        if channel_type == "send_email":
            return await self._check_email_consent(run.institution_id, contact, content_class)

        # Voice consent is phone-identity based, like SMS.
        if channel_type == "send_voice":
            return await self._check_phone_consent(
                run.institution_id, contact, ConsentChannel.VOICE.value, content_class
            )

        # Unknown channel type — allow and let the send handler decide
        return GateResult(action="allow")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _active_halt(self, institution_id: str) -> bool:
        result = await self.session.execute(
            select(OutboundEmergencyHalt)
            .where(
                OutboundEmergencyHalt.institution_id == institution_id,
                OutboundEmergencyHalt.released_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _check_sms(
        self, run: AutomationWorkflowRun, contact: Contact
    ) -> GateResult:
        phone = contact.phone
        if not phone:
            return GateResult(action="block", reason="no_phone")
        svc = SmsComplianceService(self.session)
        try:
            await svc.assert_can_send(
                institution_id=run.institution_id,
                location_id=run.location_id,
                to_number=phone,
                contact_id=run.contact_id,
            )
            return GateResult(action="allow")
        except SmsSendBlockedError as exc:
            return GateResult(action="block", reason=str(exc))

    async def _check_phone_consent(
        self,
        institution_id: str,
        contact: Contact,
        channel: str,
        content_class: str | None = None,
    ) -> GateResult:
        """Explicit consent keyed on the contact's phone (SMS/VOICE identity)."""
        phone = contact.phone
        if not phone:
            return GateResult(action="block", reason="no_phone")
        phone_hash = hash_phone(phone)
        if not phone_hash:
            return GateResult(action="block", reason="no_phone")

        result = await self.session.execute(
            select(ConsentRecord)
            .where(
                ConsentRecord.institution_id == institution_id,
                ConsentRecord.channel == channel,
                ConsentRecord.phone_hash == phone_hash,
            )
            .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
            .limit(1)
        )
        return self._resolve_consent(result.scalar_one_or_none(), channel, content_class)

    async def _check_email_consent(
        self,
        institution_id: str,
        contact: Contact,
        content_class: str | None = None,
    ) -> GateResult:
        """Explicit consent keyed on the contact's email address (EMAIL identity).

        Email-only contacts (no phone) resolve here on their email, so they are
        never falsely blocked "no_phone".
        """
        email_hash = hash_email(contact.email)
        if not email_hash:
            return GateResult(action="block", reason="no_email")

        result = await self.session.execute(
            select(ConsentRecord)
            .where(
                ConsentRecord.institution_id == institution_id,
                ConsentRecord.channel == ConsentChannel.EMAIL.value,
                ConsentRecord.email_hash == email_hash,
            )
            .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
            .limit(1)
        )
        return self._resolve_consent(
            result.scalar_one_or_none(), ConsentChannel.EMAIL.value, content_class
        )

    @staticmethod
    def _resolve_consent(
        record: ConsentRecord | None, channel: str, content_class: str | None = None
    ) -> GateResult:
        if record is None:
            # No explicit consent record. The callers guarantee the channel
            # identifier (phone/email) is on file before reaching here (they block
            # 'no_phone'/'no_email' first). Implied consent (Option B): allow
            # TRANSACTIONAL / care messages to a patient whose contact info we
            # hold; recall & marketing still require an express recorded consent.
            if content_class in _IMPLIED_CONSENT_CLASSES:
                return GateResult(action="allow", reason=f"{channel}_implied_transactional")
            return GateResult(action="block", reason=f"no_{channel}_consent")
        if record.status == ConsentStatus.REVOKED.value:
            return GateResult(action="block", reason=f"{channel}_consent_revoked")
        # Basis check (V-3): marketing-class outreach needs an express(_written) basis;
        # care reminders accept implied/exempt. A NULL/legacy basis is treated as
        # "implied", so it passes care classes but is blocked for marketing.
        acceptable = _acceptable_bases(content_class)
        record_basis = record.basis or ConsentBasis.IMPLIED.value
        if record_basis not in acceptable:
            return GateResult(action="block", reason=f"{channel}_consent_basis_insufficient")
        return GateResult(action="allow")


# Satisfy the ComplianceGate protocol at import time (structural check)
_: ComplianceGate = ComplianceGateService.__new__(ComplianceGateService)
