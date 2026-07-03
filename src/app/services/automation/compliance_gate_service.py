"""Real ComplianceGate implementation for Plan 12.

Replaces NoOpComplianceGate in the step dispatcher. Three sequential checks:
  1. Emergency halt   — institution-level kill switch (OutboundEmergencyHalt)
  2. Quiet hours      — location operating hours + timezone (hold, not block)
  3. Channel consent  — SMS via SmsComplianceService; email/voice via ConsentRecord

Hold semantics (v1): hold terminates the run with outcome "compliance_hold".
Re-queue when conditions improve is deferred.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.models.outbound_halt import OutboundEmergencyHalt
from src.app.models.sms_consent import ConsentChannel, ConsentRecord, ConsentStatus
from src.app.services.automation.compliance_gate import ComplianceGate, GateResult
from src.app.services.sms_compliance import SmsSendBlockedError, SmsComplianceService
from src.app.services.sms_privacy import hash_phone

logger = logging.getLogger(__name__)


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
    ) -> GateResult:
        """Run all compliance checks in priority order."""
        # 1. Emergency halt — blocks everything for the institution
        if await self._active_halt(run.institution_id):
            logger.info(
                "compliance gate: blocked by emergency halt institution=%s run=%s",
                run.institution_id, run.id,
            )
            return GateResult(action="block", reason="emergency_halt")

        # 2. Quiet hours — hold until clinic is open
        if run.location_id and await self._is_quiet_hours(run.location_id, now=now):
            logger.info(
                "compliance gate: hold quiet_hours location=%s run=%s",
                run.location_id, run.id,
            )
            return GateResult(action="hold", reason="quiet_hours")

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
            return await self._check_sms(run, contact)

        if channel_type in ("send_email", "send_voice"):
            channel = ConsentChannel.EMAIL.value if channel_type == "send_email" else ConsentChannel.VOICE.value
            return await self._check_explicit_consent(run.institution_id, contact, channel)

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

    async def _is_quiet_hours(
        self,
        location_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        location = await self.session.get(InstitutionLocation, location_id)
        if location is None:
            return False

        try:
            tz = ZoneInfo(location.timezone or "UTC")
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")

        now_local = (now or datetime.now(tz=timezone.utc)).astimezone(tz)
        day = now_local.weekday()  # 0=Mon … 6=Sun (ISO, matches LocationOperatingHours)

        result = await self.session.execute(
            select(LocationOperatingHours)
            .where(
                LocationOperatingHours.location_id == location_id,
                LocationOperatingHours.day_of_week == day,
            )
            .limit(1)
        )
        hours = result.scalar_one_or_none()
        if hours is None:
            return False  # unconfigured → no restriction
        if not hours.is_open:
            return True  # clinic closed today
        if hours.open_time and now_local.time() < hours.open_time:
            return True
        if hours.close_time and now_local.time() > hours.close_time:
            return True
        return False

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

    async def _check_explicit_consent(
        self,
        institution_id: str,
        contact: Contact,
        channel: str,
    ) -> GateResult:
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
        record = result.scalar_one_or_none()
        if record is None:
            return GateResult(action="block", reason=f"no_{channel}_consent")
        if record.status == ConsentStatus.REVOKED.value:
            return GateResult(action="block", reason=f"{channel}_consent_revoked")
        return GateResult(action="allow")


# Satisfy the ComplianceGate protocol at import time (structural check)
_: ComplianceGate = ComplianceGateService.__new__(ComplianceGateService)
