"""Tenant-aware messaging credential resolution (Plan 10).

Single source of truth for resolving per-institution Twilio and email-sender
credentials with platform-level fallback. Extracted from the inline logic that
previously lived in :class:`SmsService` and :class:`EmailNodeExecutor` so that
SMS sending, email sending, channel-readiness checks, and inbound-webhook
signature validation all resolve credentials identically.

Resolution rule (unchanged from the original inline logic):
  * Twilio account SID / auth token: institution sub-account credential when
    set, else the platform-level ``TWILLIO_SID`` / ``TWILLIO_API_SECRET``.
  * Email from-address: institution ``email_from_address`` when set, else the
    platform ``RESEND_FROM_EMAIL``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation


@dataclass(frozen=True)
class ResolvedSmsCredentials:
    """Resolved Twilio credentials for an outbound SMS."""

    account_sid: str | None
    auth_token: str | None
    from_number: str | None
    # True when the credentials come from an institution Twilio sub-account
    # (both SID and token present on the institution), False for platform creds.
    is_subaccount: bool


@dataclass(frozen=True)
class ResolvedEmailFrom:
    """Resolved email sending identity for an outbound email."""

    from_address: str | None
    from_name: str | None
    # True when the from-address comes from the institution (vs. platform).
    is_institution: bool


class TenantTwilioCredentialResolver:
    """Resolves per-tenant Twilio/email credentials with platform fallback.

    Stateless resolution (``resolve_sms`` / ``resolve_email_from``) is exposed as
    static methods so callers that already hold the institution/location can use
    them without a session. The inbound-webhook token helper needs a session to
    map a phone number back to its owning institution.
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Stateless resolution (institution → platform fallback)
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_sms(
        institution: Institution | None,
        location: InstitutionLocation | None,
    ) -> ResolvedSmsCredentials:
        """Resolve Twilio SMS credentials + sender number.

        Preserves the original per-field fallback semantics from
        ``SmsService._get_twilio_client``: the account SID and auth token each
        fall back to the platform value independently. ``is_subaccount`` is True
        only when the institution carries *both* a sub-account SID and token.
        """
        inst_sid = institution.twilio_account_sid if institution else None
        inst_token = institution.twilio_auth_token if institution else None

        account_sid = inst_sid or settings.twillio_sid
        auth_token = inst_token or settings.twillio_api_secret
        from_number = (location.twilio_from_number if location else None) or None

        return ResolvedSmsCredentials(
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            is_subaccount=bool(inst_sid and inst_token),
        )

    @staticmethod
    def resolve_email_from(
        institution: Institution | None,
        location: InstitutionLocation | None = None,
    ) -> ResolvedEmailFrom:
        """Resolve the email from-address/name (institution → platform fallback).

        ``location`` is accepted for signature symmetry with :meth:`resolve_sms`
        and future per-location sender identities; there is no per-location email
        override today.
        """
        inst_address = institution.email_from_address if institution else None
        from_address = inst_address or settings.resend_from_email
        from_name = institution.email_from_name if institution else None
        return ResolvedEmailFrom(
            from_address=from_address,
            from_name=from_name,
            is_institution=bool(inst_address),
        )

    # ------------------------------------------------------------------
    # Inbound-webhook signature-token resolution
    # ------------------------------------------------------------------

    async def resolve_auth_token(self, *candidate_numbers: str | None) -> str | None:
        """Resolve the Twilio auth token to validate a webhook signature.

        Twilio signs a webhook with the auth token of the (sub-)account that owns
        the number involved. The owning number is the *To* for inbound SMS and the
        *From* for outbound status callbacks, so callers pass both candidates; the
        first one that maps to an institution sub-account token wins. Falls back to
        the platform token when no candidate belongs to a sub-account — keeping
        behavior unchanged for tenants without sub-account credentials.
        """
        if self.session is not None:
            for number in candidate_numbers:
                if not number:
                    continue
                institution = await self._institution_for_number(number)
                if institution and institution.twilio_auth_token:
                    return institution.twilio_auth_token
        return settings.twillio_api_secret

    async def _institution_for_number(self, number: str) -> Institution | None:
        """Return the active institution owning ``number`` as a location sender."""
        assert self.session is not None
        return (
            (
                await self.session.execute(
                    select(Institution)
                    .join(
                        InstitutionLocation,
                        InstitutionLocation.institution_id == Institution.id,
                    )
                    .where(
                        InstitutionLocation.twilio_from_number == number,
                        InstitutionLocation.is_active.is_(True),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
