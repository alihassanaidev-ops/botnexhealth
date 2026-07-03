"""Channel-readiness checker (Plan 10, rescoped MVP).

Implements the :class:`ChannelReadinessChecker` seam declared in
``validation_service``. It inspects which channels a workflow definition uses
(send_sms / send_email / send_voice) and emits **WARNING-severity** issues when
the channel isn't provisioned for the target location. Readiness is *computed*
from existing credentials (Twilio sender number / sub-account creds, email
from-address, per-node Retell agent) — there is no readiness state table.

Warnings only: provisioning (A2P registration, sub-account setup, sender-number
assignment) is still manual in this MVP, so a not-ready channel must NOT block
publishing. Institution-level / template validation (``location_id is None``)
returns no issues, since readiness is a per-location property.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.validation_service import ValidationIssue
from src.app.services.messaging_credentials import TenantTwilioCredentialResolver

_READINESS_CODE = "channel_not_ready"


@dataclass(frozen=True)
class ChannelReadinessReport:
    """Per-location channel readiness for the builder's pre-publish setup panel."""

    sms: bool
    email: bool
    voice_configurable: bool
    # [{channel, ready, reason}] — reason is None when the channel is ready.
    details: list[dict] = field(default_factory=list)


class ChannelReadinessService:
    """Warns when a channel used by the workflow isn't provisioned for the location."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def check(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[ValidationIssue]:
        # Readiness is a per-location property. Institution-level / template
        # validation has no location to check against, so emit nothing.
        if location_id is None:
            return []

        sms_nodes = [n for n in definition.nodes if isinstance(n, SendSmsNode)]
        email_nodes = [n for n in definition.nodes if isinstance(n, SendEmailNode)]
        voice_nodes = [n for n in definition.nodes if isinstance(n, SendVoiceNode)]
        if not (sms_nodes or email_nodes or voice_nodes):
            return []

        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, location_id)
            if self.session is not None
            else None
        )
        institution: Institution | None = (
            await self.session.get(Institution, institution_id)
            if self.session is not None and institution_id
            else None
        )

        issues: list[ValidationIssue] = []

        if sms_nodes and not self._sms_ready(institution, location):
            for node in sms_nodes:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=(
                            "SMS channel is not provisioned for this location: no "
                            "Twilio sender number (or platform Twilio credentials) is "
                            "configured. Messages will fail until it is set up."
                        ),
                    )
                )

        if email_nodes and not self._email_ready(institution):
            for node in email_nodes:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=(
                            "Email channel is not provisioned: no from-address "
                            "(institution or platform) is configured. Emails will fail "
                            "until a sender address is set up."
                        ),
                    )
                )

        for node in voice_nodes:
            # Voice readiness is carried on the node itself (retell_agent_id is a
            # required, non-empty field on SendVoiceNode), so a structurally valid
            # definition is voice-configurable. Warn only if it is somehow blank.
            if not (node.retell_agent_id or "").strip():
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=(
                            "Voice channel is not configured: this node has no Retell "
                            "agent id. Calls will fail until an agent is assigned."
                        ),
                    )
                )

        return issues

    async def readiness_for_location(
        self, *, institution_id: str, location_id: str
    ) -> ChannelReadinessReport:
        """Compute channel readiness for a location, independent of any workflow.

        Powers ``GET /automation/workflows/channel-readiness`` so the builder can
        surface missing setup before publish.
        """
        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, location_id)
            if self.session is not None
            else None
        )
        institution: Institution | None = (
            await self.session.get(Institution, institution_id)
            if self.session is not None and institution_id
            else None
        )

        sms_ready = self._sms_ready(institution, location)
        email_ready = self._email_ready(institution)
        voice_ready = bool(location and (location.retell_agent_id or "").strip())

        details = [
            {
                "channel": "sms",
                "ready": sms_ready,
                "reason": None
                if sms_ready
                else "No Twilio sender number (or platform credentials) for this location.",
            },
            {
                "channel": "email",
                "ready": email_ready,
                "reason": None
                if email_ready
                else "No email from-address (institution or platform) configured.",
            },
            {
                "channel": "voice",
                "ready": voice_ready,
                "reason": None
                if voice_ready
                else "No Retell agent assigned to this location.",
            },
        ]
        return ChannelReadinessReport(
            sms=sms_ready,
            email=email_ready,
            voice_configurable=voice_ready,
            details=details,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _sms_ready(
        institution: Institution | None, location: InstitutionLocation | None
    ) -> bool:
        """SMS is ready when the location has a sender number and Twilio creds
        (institution sub-account or platform fallback) exist to send with."""
        creds = TenantTwilioCredentialResolver.resolve_sms(institution, location)
        return bool(creds.from_number and creds.account_sid and creds.auth_token)

    @staticmethod
    def _email_ready(institution: Institution | None) -> bool:
        """Email is ready when a from-address (institution or platform) resolves
        and the Resend API key is configured to send with."""
        email_from = TenantTwilioCredentialResolver.resolve_email_from(institution)
        return bool(email_from.from_address and settings.resend_api_key)
