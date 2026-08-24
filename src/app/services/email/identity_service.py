"""Resolve, provision and re-verify a clinic's sending identity.

Resolution prefers the most specific identity that can actually deliver:
location → institution → the address on the Institution row → platform default.

Only a **verified** identity is used. A pending or revoked one falls back to the
platform address rather than sending from a domain that would fail
authentication — unauthenticated mail from the clinic's own domain is worse than
recognisable mail from ours, because it lands in spam silently and damages the
clinic's domain reputation while it does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.email_sending_identity import (
    EmailIdentityStatus,
    EmailSendingIdentity,
)
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.email.ses_provisioning import (
    DnsRecord,
    SesProvisioningError,
    SesProvisioningService,
)

logger = logging.getLogger(__name__)

#: How long a domain may sit unverified before it is treated as failed. DKIM
#: propagation is usually minutes when we own the zone; three days is generous
#: for a clinic-owned domain waiting on their IT.
VERIFICATION_TIMEOUT = timedelta(hours=72)

#: Re-check cadence for an identity that is already verified, so DNS removed
#: later is noticed rather than discovered through a deliverability collapse.
VERIFIED_RECHECK_INTERVAL = timedelta(hours=24)

#: Local part of the generated from-address.
DEFAULT_LOCAL_PART = "hello"


@dataclass(frozen=True)
class ResolvedSendingIdentity:
    from_address: str | None
    from_name: str | None
    reply_to: str | None
    provider: str
    tenant_name: str | None = None
    configuration_set: str | None = None
    #: True when no verified clinic identity applied and the platform address
    #: was used instead.
    is_platform_fallback: bool = True
    #: The row that produced this, when one did.
    identity_id: str | None = None

    @property
    def is_sendable(self) -> bool:
        return bool(self.from_address)


class EmailIdentityService:
    def __init__(
        self,
        session: AsyncSession,
        provisioning: SesProvisioningService | None = None,
    ) -> None:
        self.session = session
        self.provisioning = provisioning or SesProvisioningService()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def resolve(
        self, institution_id: str, location_id: str | None = None
    ) -> ResolvedSendingIdentity:
        """The identity a send from this institution/location should use."""
        identity = await self.get_effective_identity(institution_id, location_id)
        if identity is not None and identity.is_sendable:
            return ResolvedSendingIdentity(
                from_address=identity.from_address,
                from_name=identity.from_name,
                reply_to=identity.reply_to_address or settings.resend_reply_to,
                provider=identity.provider,
                tenant_name=identity.provider_tenant_name,
                configuration_set=identity.provider_configuration_set,
                is_platform_fallback=False,
                identity_id=str(identity.id),
            )

        if identity is not None:
            logger.warning(
                "sending identity %s is %s; falling back to the platform address",
                identity.domain,
                identity.status,
            )

        # Legacy per-institution address, set before identities existed. It has
        # no verification state, so it is used only as a fallback.
        institution = await self.session.get(Institution, institution_id)
        legacy_address = getattr(institution, "email_from_address", None)
        if legacy_address:
            return ResolvedSendingIdentity(
                from_address=legacy_address,
                from_name=getattr(institution, "email_from_name", None),
                reply_to=settings.resend_reply_to,
                provider=settings.patient_email_provider,
                is_platform_fallback=False,
            )

        return ResolvedSendingIdentity(
            from_address=settings.resend_from_email,
            from_name=None,
            reply_to=settings.resend_reply_to,
            provider=settings.patient_email_provider,
            is_platform_fallback=True,
        )

    async def get_effective_identity(
        self, institution_id: str, location_id: str | None = None
    ) -> EmailSendingIdentity | None:
        """The most specific configured identity, verified or not."""
        if location_id:
            result = await self.session.execute(
                select(EmailSendingIdentity).where(
                    EmailSendingIdentity.institution_id == institution_id,
                    EmailSendingIdentity.location_id == location_id,
                )
            )
            located = result.scalar_one_or_none()
            if located is not None:
                return located

        result = await self.session.execute(
            select(EmailSendingIdentity).where(
                EmailSendingIdentity.institution_id == institution_id,
                EmailSendingIdentity.location_id.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_institution(
        self, institution_id: str
    ) -> list[EmailSendingIdentity]:
        result = await self.session.execute(
            select(EmailSendingIdentity)
            .where(EmailSendingIdentity.institution_id == institution_id)
            .order_by(EmailSendingIdentity.location_id.nulls_first())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    async def provision(
        self,
        *,
        institution_id: str,
        location_id: str | None = None,
        from_name: str | None = None,
        reply_to_address: str | None = None,
        local_part: str = DEFAULT_LOCAL_PART,
    ) -> EmailSendingIdentity:
        """Create the provider identity and record it.

        Re-provisioning an existing row is allowed: SES returns the current DKIM
        tokens for a domain that already exists, so this is safe to retry after
        a partial failure.
        """
        institution = await self.session.get(Institution, institution_id)
        if institution is None:
            raise SesProvisioningError("Institution not found")

        location: InstitutionLocation | None = None
        if location_id:
            location = await self.session.get(InstitutionLocation, location_id)
            if location is None:
                raise SesProvisioningError("Location not found")

        slug = _identity_slug(institution, location)
        display_name = from_name or (location.name if location else institution.name)

        provisioned = await self.provisioning.provision(
            slug=slug, institution_id=str(institution_id)
        )

        identity = await self.get_effective_identity(institution_id, location_id)
        # get_effective_identity falls back to the institution row, which must
        # not be overwritten when provisioning a location.
        if identity is not None and str(identity.location_id or "") != str(location_id or ""):
            identity = None

        if identity is None:
            identity = EmailSendingIdentity(
                institution_id=institution_id,
                location_id=location_id,
            )
            self.session.add(identity)

        identity.provider = "ses"
        identity.domain = provisioned.domain
        identity.from_address = f"{local_part}@{provisioned.domain}"
        identity.from_name = display_name
        identity.reply_to_address = reply_to_address
        identity.dns_records = [r.as_dict() for r in provisioned.dns_records]
        identity.provider_tenant_name = provisioned.tenant_name
        identity.provider_configuration_set = provisioned.configuration_set
        identity.status = (
            EmailIdentityStatus.VERIFYING.value
            if provisioned.dns_published
            else EmailIdentityStatus.PENDING_DNS.value
        )
        identity.failure_reason = None
        identity.verified_at = None
        identity.last_checked_at = None

        await self.session.flush()
        return identity

    async def deprovision(self, identity: EmailSendingIdentity) -> None:
        records = [DnsRecord(**r) for r in (identity.dns_records or [])]
        await self.provisioning.teardown(
            domain=identity.domain,
            dns_records=records or None,
            tenant_name=identity.provider_tenant_name,
            configuration_set=identity.provider_configuration_set,
        )
        await self.session.delete(identity)
        await self.session.flush()

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    async def refresh(
        self, identity: EmailSendingIdentity, *, now: datetime | None = None
    ) -> EmailSendingIdentity:
        """Re-check the provider and move the identity through its lifecycle."""
        now = now or datetime.now(timezone.utc)
        status, detail = await self.provisioning.check_status(identity.domain)
        identity.last_checked_at = now

        if status == "SUCCESS":
            if identity.status != EmailIdentityStatus.VERIFIED.value:
                logger.info("sending identity verified: %s", identity.domain)
            identity.status = EmailIdentityStatus.VERIFIED.value
            identity.verified_at = identity.verified_at or now
            identity.failure_reason = None

        elif status in ("FAILED", "NOT_FOUND"):
            identity.status = (
                EmailIdentityStatus.REVOKED.value
                if identity.verified_at
                else EmailIdentityStatus.FAILED.value
            )
            identity.failure_reason = detail or f"Provider reported {status}"

        else:  # PENDING / TEMPORARY_FAILURE
            if identity.verified_at:
                # It used to verify and no longer does — almost always the DNS
                # records were removed. Distinct from never having verified,
                # because mail *was* flowing and is now failing authentication.
                identity.status = EmailIdentityStatus.REVOKED.value
                identity.failure_reason = (
                    detail or "Domain no longer verifies; DNS records may have been removed"
                )
            elif now - _as_utc(identity.created_at) > VERIFICATION_TIMEOUT:
                identity.status = EmailIdentityStatus.FAILED.value
                identity.failure_reason = (
                    detail or "Domain did not verify within 72 hours"
                )
            else:
                identity.status = EmailIdentityStatus.VERIFYING.value
                identity.failure_reason = detail

        await self.session.flush()
        return identity

    async def due_for_check(self, *, now: datetime | None = None, limit: int = 100):
        """Identities the sweep should re-check."""
        now = now or datetime.now(timezone.utc)
        pending = (
            EmailIdentityStatus.PENDING_DNS.value,
            EmailIdentityStatus.VERIFYING.value,
        )
        result = await self.session.execute(
            select(EmailSendingIdentity)
            .where(
                (EmailSendingIdentity.status.in_(pending))
                | (
                    (EmailSendingIdentity.status == EmailIdentityStatus.VERIFIED.value)
                    & (
                        EmailSendingIdentity.last_checked_at.is_(None)
                        | (
                            EmailSendingIdentity.last_checked_at
                            < now - VERIFIED_RECHECK_INTERVAL
                        )
                    )
                )
            )
            .order_by(EmailSendingIdentity.last_checked_at.nulls_first())
            .limit(limit)
        )
        return list(result.scalars().all())


def _identity_slug(
    institution: Institution, location: InstitutionLocation | None
) -> str:
    """Subdomain label for this scope.

    A location identity includes the location slug so two offices of the same
    practice get distinct sending domains and separate reputations.
    """
    base = getattr(institution, "slug", None) or str(institution.id)[:8]
    if location is None:
        return str(base)
    loc = getattr(location, "slug", None) or str(location.id)[:8]
    return f"{base}-{loc}"


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
