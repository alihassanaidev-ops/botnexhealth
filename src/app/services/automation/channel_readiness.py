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

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.outbound_voice import OutboundVoiceProfile
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.validation_service import ValidationIssue
from src.app.services.messaging_credentials import TenantTwilioCredentialResolver
from sqlalchemy import select

logger = logging.getLogger(__name__)

_READINESS_CODE = "channel_not_ready"
_PLACEHOLDER_VALUES = {
    "acxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "your-twilio-auth-token",
    "re_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "alerts@yourdomain.com",
    "support@yourdomain.com",
}


def _has_real_value(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    if normalized in _PLACEHOLDER_VALUES:
        return False
    if normalized.startswith("your-") or "yourdomain.com" in normalized:
        return False
    if "xxxxxxxx" in normalized:
        return False
    return True


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

        if email_nodes:
            problem = await self._email_problem(institution, location)
            for node in email_nodes:
                if problem is None:
                    continue
                severity, message = problem
                issues.append(
                    ValidationIssue(
                        severity=severity,
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=message,
                    )
                )

        for node in voice_nodes:
            issues += await self._voice_node_issues(node, location)

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
        email_problem = await self._email_problem(institution, location)
        email_ready = email_problem is None
        email_reason = email_problem[1] if email_problem else None
        voice_ready = await self._location_has_usable_outbound_profile(location)

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
                # Carries the specific reason — an unverified domain and a
                # missing address are very different problems to act on.
                "reason": email_reason,
            },
            {
                "channel": "voice",
                "ready": voice_ready,
                "reason": None
                if voice_ready
                else "No active outbound voice profile with a Retell agent is configured for this location.",
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
        return bool(
            _has_real_value(creds.from_number)
            and _has_real_value(creds.account_sid)
            and _has_real_value(creds.auth_token)
        )

    async def _email_problem(
        self,
        institution: Institution | None,
        location: InstitutionLocation | None,
    ) -> tuple[str, str] | None:
        """``(severity, message)`` for the email channel, or None when ready.

        A **configured but unverified** sending domain is an error, not a
        warning. Unlike a missing sender — which fails loudly and visibly — an
        unverified domain sends successfully and then lands in spam. Nobody sees
        an error; the clinic just quietly stops reaching patients, and the
        domain's reputation is damaged in the process. Blocking at publish is
        the only point where that is cheap to catch.
        """
        from src.app.models.email_sending_identity import (
            EmailIdentityStatus,
            EmailSendingIdentity,
        )

        if institution is None:
            return None

        identity: EmailSendingIdentity | None = None
        try:
            from src.app.services.email.identity_service import EmailIdentityService

            identity = await EmailIdentityService(self.session).get_effective_identity(
                str(institution.id),
                str(location.id) if location is not None else None,
            )
        except Exception:  # noqa: BLE001 — readiness must not fail the publish path
            logger.warning("could not load the sending identity for readiness", exc_info=True)

        if identity is not None and identity.status != EmailIdentityStatus.VERIFIED.value:
            if identity.status == EmailIdentityStatus.REVOKED.value:
                return (
                    "error",
                    f"The sending domain {identity.domain} has stopped verifying — its "
                    "DNS records may have been removed. Email would be delivered "
                    "unauthenticated and land in spam. Re-verify it before publishing.",
                )
            return (
                "error",
                f"The sending domain {identity.domain} is not verified yet "
                f"(status: {identity.status}). Email sent from an unverified domain "
                "fails authentication and lands in spam without reporting an error.",
            )

        if identity is not None:
            return None

        # No per-clinic identity: fall back to the legacy address plus a provider
        # key. Still only a warning — mail from the platform address delivers.
        email_from = TenantTwilioCredentialResolver.resolve_email_from(institution)
        ready = bool(
            _has_real_value(email_from.from_address)
            and _has_real_value(settings.resend_api_key)
        )
        if ready:
            return None
        return (
            "warning",
            "Email channel is not provisioned: no from-address (institution or "
            "platform) is configured. Emails will fail until a sender address is "
            "set up.",
        )

    async def _voice_node_issues(
        self,
        node: SendVoiceNode,
        location: InstitutionLocation | None,
    ) -> list[ValidationIssue]:
        if not self.session:
            return []

        profile_id = (node.voice_profile_id or "").strip()
        legacy_agent_id = (node.retell_agent_id or "").strip()
        if not profile_id and not legacy_agent_id:
            return [
                ValidationIssue(
                    severity="error",
                    code=_READINESS_CODE,
                    node_id=node.id,
                    message=(
                        "Voice step has no outbound voice profile or Retell agent selected."
                    ),
                )
            ]

        if profile_id:
            profile = await self._get_active_profile(profile_id, location)
            if profile is None:
                return [
                    ValidationIssue(
                        severity="error",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=(
                            "Selected outbound voice profile is missing, inactive, "
                            "or not attached to this location."
                        ),
                    )
                ]

            issues: list[ValidationIssue] = []
            if not _has_real_value(profile.retell_agent_id):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message="Selected outbound voice profile has no Retell agent id.",
                    )
                )
            if not _has_real_value(profile.retell_from_number) and not _has_real_value(
                location.retell_from_number if location else None
            ):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code=_READINESS_CODE,
                        node_id=node.id,
                        message=(
                            "Selected outbound voice profile has no from-number, and "
                            "the location has no Retell outbound from-number fallback."
                        ),
                    )
                )
            return issues

        if not _has_real_value(location.retell_from_number if location else None):
            return [
                ValidationIssue(
                    severity="error",
                    code=_READINESS_CODE,
                    node_id=node.id,
                    message="Voice step has no Retell outbound from-number for this location.",
                )
            ]
        return []

    async def _get_active_profile(
        self,
        profile_id: str,
        location: InstitutionLocation | None,
    ) -> OutboundVoiceProfile | None:
        if not self.session or not location:
            return None
        return (
            await self.session.execute(
                select(OutboundVoiceProfile).where(
                    OutboundVoiceProfile.id == profile_id,
                    OutboundVoiceProfile.location_id == str(location.id),
                    OutboundVoiceProfile.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def _location_has_usable_outbound_profile(
        self,
        location: InstitutionLocation | None,
    ) -> bool:
        if not self.session or not location:
            return False
        return (
            await self.session.execute(
                select(OutboundVoiceProfile.id)
                .where(
                    OutboundVoiceProfile.location_id == str(location.id),
                    OutboundVoiceProfile.is_active.is_(True),
                    OutboundVoiceProfile.retell_agent_id.is_not(None),
                    OutboundVoiceProfile.retell_agent_id != "",
                )
                .limit(1)
            )
        ).scalar() is not None
