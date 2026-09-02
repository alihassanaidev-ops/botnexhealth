"""Resolve, provision and re-verify clinic domains and sender addresses.

Resolution prefers a location default, then an institution default, then the
legacy Institution address and finally the platform fallback. A workflow may
pin one address; pinned selections fail closed instead of changing brands.

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

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.email_sending_identity import (
    EmailIdentityStatus,
    EmailSendingIdentity,
)
from src.app.models.email_sender_address import EmailSenderAddress
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.email.ses_provisioning import (
    DnsRecord,
    SesProvisioningError,
    SesProvisioningService,
    normalize_domain,
    subdomain_for,
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
    address_id: str | None = None
    inbound_domain: str | None = None

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
        self,
        institution_id: str,
        location_id: str | None = None,
        sender_address_id: str | None = None,
    ) -> ResolvedSendingIdentity:
        """The identity a send from this institution/location should use."""
        address, identity = await self.get_effective_sender(
            institution_id, location_id, sender_address_id=sender_address_id
        )
        if (
            identity is not None
            and address is not None
            and address.is_active
            and identity.is_sendable
            and _provider_enabled(identity)
        ):
            return ResolvedSendingIdentity(
                from_address=address.from_address,
                from_name=address.from_name,
                reply_to=address.external_reply_to or settings.resend_reply_to,
                provider=identity.provider,
                tenant_name=identity.provider_tenant_name,
                configuration_set=identity.provider_configuration_set,
                is_platform_fallback=False,
                identity_id=str(identity.id),
                address_id=str(address.id),
                inbound_domain=(identity.inbound_domain if identity.inbound_enabled else None),
            )

        # A workflow that explicitly selected a clinic address must never
        # silently change brands. Missing, disabled, or unverified pins fail the
        # node and are surfaced by publish/readiness checks.
        if sender_address_id:
            return ResolvedSendingIdentity(
                from_address=None,
                from_name=None,
                reply_to=None,
                provider=identity.provider if identity is not None else settings.patient_email_provider,
                is_platform_fallback=False,
                identity_id=str(identity.id) if identity is not None else None,
                address_id=str(address.id) if address is not None else None,
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

    async def get_effective_sender(
        self,
        institution_id: str,
        location_id: str | None = None,
        *,
        sender_address_id: str | None = None,
    ) -> tuple[EmailSenderAddress | None, EmailSendingIdentity | None]:
        """Resolve an explicit address or the location/institution default."""
        base = (
            select(EmailSenderAddress, EmailSendingIdentity)
            .join(
                EmailSendingIdentity,
                EmailSendingIdentity.id == EmailSenderAddress.email_identity_id,
            )
            .where(EmailSenderAddress.institution_id == institution_id)
        )
        if sender_address_id:
            row = (
                await self.session.execute(
                    base.where(EmailSenderAddress.id == sender_address_id)
                )
            ).first()
            if row is None:
                return None, None
            address, identity = row
            if address.location_id and str(address.location_id) != str(location_id or ""):
                return None, None
            return address, identity

        if location_id:
            row = (
                await self.session.execute(
                    base.where(
                        EmailSenderAddress.location_id == location_id,
                        EmailSenderAddress.is_default.is_(True),
                    )
                )
            ).first()
            if row is not None:
                return row[0], row[1]

        row = (
            await self.session.execute(
                base.where(
                    EmailSenderAddress.location_id.is_(None),
                    EmailSenderAddress.is_default.is_(True),
                )
            )
        ).first()
        return (row[0], row[1]) if row is not None else (None, None)

    async def get_effective_identity(
        self, institution_id: str, location_id: str | None = None
    ) -> EmailSendingIdentity | None:
        """Compatibility helper returning the domain behind the effective address."""
        _address, identity = await self.get_effective_sender(institution_id, location_id)
        return identity

    async def list_for_institution(
        self, institution_id: str
    ) -> list[EmailSendingIdentity]:
        result = await self.session.execute(
            select(EmailSendingIdentity)
            .where(EmailSendingIdentity.institution_id == institution_id)
            .order_by(func.lower(EmailSendingIdentity.domain))
        )
        return list(result.scalars().all())

    async def list_addresses(
        self, institution_id: str
    ) -> list[EmailSenderAddress]:
        result = await self.session.execute(
            select(EmailSenderAddress)
            .where(EmailSenderAddress.institution_id == institution_id)
            .order_by(
                EmailSenderAddress.location_id.nulls_first(),
                EmailSenderAddress.is_default.desc(),
                func.lower(EmailSenderAddress.from_address),
            )
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
        domain: str | None = None,
        inbound_domain: str | None = None,
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
            if str(location.institution_id) != str(institution_id):
                raise SesProvisioningError(
                    "Location does not belong to the selected institution"
                )

        slug = _identity_slug(institution, location)
        display_name = from_name or (location.name if location else institution.name)

        requested_domain = normalize_domain(domain) if domain else None
        if requested_domain is None and settings.ses_sending_domain:
            requested_domain = subdomain_for(slug, settings.ses_sending_domain)

        identity: EmailSendingIdentity | None = None
        if requested_domain:
            identity = (
                await self.session.execute(
                    select(EmailSendingIdentity).where(
                        func.lower(EmailSendingIdentity.domain) == requested_domain
                    )
                )
            ).scalar_one_or_none()
            if identity is not None and str(identity.institution_id) != str(institution_id):
                raise SesProvisioningError(
                    "That email domain is already owned by another institution"
                )
            receiving_collision = (
                await self.session.execute(
                    select(EmailSendingIdentity).where(
                        func.lower(EmailSendingIdentity.inbound_domain)
                        == requested_domain
                    )
                )
            ).scalar_one_or_none()
            if receiving_collision is not None:
                raise SesProvisioningError(
                    "That domain is already registered as a receiving domain"
                )

        requested_inbound = (
            normalize_domain(inbound_domain) if inbound_domain else None
        )
        if (
            identity is not None
            and requested_inbound
            and identity.inbound_domain
            and requested_inbound != identity.inbound_domain.lower()
        ):
            raise SesProvisioningError(
                "This sending domain already has a different receiving subdomain; "
                "deactivate and remove it before changing DNS ownership"
            )
        effective_inbound = requested_inbound or (
            identity.inbound_domain if identity is not None else None
        )
        if effective_inbound:
            inbound_owner = (
                await self.session.execute(
                    select(EmailSendingIdentity).where(
                        or_(
                            func.lower(EmailSendingIdentity.domain)
                            == effective_inbound,
                            func.lower(EmailSendingIdentity.inbound_domain)
                            == effective_inbound,
                        )
                    )
                )
            ).scalar_one_or_none()
            if inbound_owner is not None and inbound_owner is not identity:
                raise SesProvisioningError(
                    "That receiving domain is already registered"
                )

        provisioned = await self.provisioning.provision(
            slug=slug,
            institution_id=str(institution_id),
            domain=domain,
            inbound_domain=effective_inbound,
            tenant_name=(identity.provider_tenant_name if identity is not None else None),
            configuration_set=(
                identity.provider_configuration_set if identity is not None else None
            ),
        )

        if identity is None:
            identity = (
                await self.session.execute(
                    select(EmailSendingIdentity).where(
                        or_(
                            func.lower(EmailSendingIdentity.domain)
                            == provisioned.domain.lower(),
                            func.lower(EmailSendingIdentity.inbound_domain)
                            == provisioned.domain.lower(),
                        )
                    )
                )
            ).scalar_one_or_none()
        if identity is not None and str(identity.institution_id) != str(institution_id):
            raise SesProvisioningError("That email domain is already owned by another institution")

        is_new = identity is None
        if is_new:
            identity = EmailSendingIdentity(
                institution_id=institution_id,
                location_id=None,
            )
            self.session.add(identity)

        identity.provider = "ses"
        identity.domain = provisioned.domain
        identity.from_address = f"{local_part}@{provisioned.domain}"
        identity.from_name = display_name
        identity.reply_to_address = reply_to_address
        identity.inbound_domain = provisioned.inbound_domain
        identity.inbound_dns_records = [
            r.as_dict() for r in provisioned.inbound_dns_records
        ]
        if is_new or identity.inbound_domain != provisioned.inbound_domain:
            identity.inbound_enabled = False
        identity.dns_records = [r.as_dict() for r in provisioned.dns_records]
        identity.dns_managed = provisioned.dns_published
        identity.provider_tenant_name = provisioned.tenant_name
        identity.provider_configuration_set = provisioned.configuration_set
        if is_new:
            identity.status = (
                EmailIdentityStatus.VERIFYING.value
                if provisioned.dns_published
                else EmailIdentityStatus.PENDING_DNS.value
            )
            # Provisioning never changes live routing by itself. A super admin
            # activates only after verification and the platform safety gate.
            identity.is_active = False
            identity.failure_reason = None
            identity.verified_at = None
            identity.last_checked_at = None

        await self.session.flush()
        address = (
            await self.session.execute(
                select(EmailSenderAddress).where(
                    EmailSenderAddress.email_identity_id == identity.id,
                    func.lower(EmailSenderAddress.from_address)
                    == identity.from_address.lower(),
                )
            )
        ).scalar_one_or_none()
        if address is None:
            address = await self.create_address(
                identity,
                institution_id=str(institution_id),
                location_id=str(location_id) if location_id else None,
                local_part=local_part,
                from_name=display_name,
                external_reply_to=reply_to_address,
                make_default=None,
            )
        return identity

    async def create_address(
        self,
        identity: EmailSendingIdentity,
        *,
        institution_id: str,
        location_id: str | None,
        local_part: str,
        from_name: str | None = None,
        external_reply_to: str | None = None,
        make_default: bool | None = None,
    ) -> EmailSenderAddress:
        if str(identity.institution_id) != str(institution_id):
            raise SesProvisioningError("Domain does not belong to this institution")
        if location_id:
            location = await self.session.get(InstitutionLocation, location_id)
            if location is None or str(location.institution_id) != str(institution_id):
                raise SesProvisioningError("Location does not belong to this institution")
        local = (local_part or "").strip().lower()
        if not local or not local.isascii() or len(local) > 64 or not all(
            c.isalnum() or c in "._-" for c in local
        ):
            raise SesProvisioningError("Address prefix may contain letters, numbers, dot, underscore, and hyphen")
        from_address = f"{local}@{identity.domain}".lower()
        exists = (
            await self.session.execute(
                select(EmailSenderAddress).where(
                    func.lower(EmailSenderAddress.from_address) == from_address
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            if str(exists.institution_id) != str(institution_id):
                raise SesProvisioningError("That sender address is already registered")
            return exists

        has_default = (
            await self.session.execute(
                select(EmailSenderAddress.id).where(
                    EmailSenderAddress.institution_id == institution_id,
                    EmailSenderAddress.location_id == location_id
                    if location_id
                    else EmailSenderAddress.location_id.is_(None),
                    EmailSenderAddress.is_default.is_(True),
                )
            )
        ).scalar_one_or_none()
        should_make_default = has_default is None or bool(make_default)
        address = EmailSenderAddress(
            institution_id=institution_id,
            email_identity_id=str(identity.id),
            location_id=location_id,
            local_part=local,
            from_address=from_address,
            from_name=from_name,
            external_reply_to=external_reply_to,
            is_active=True,
            # Insert as non-default first.  If another default already exists,
            # inserting this row as default would trip the partial unique index
            # before ``set_default`` gets a chance to clear the old row.
            is_default=False,
        )
        self.session.add(address)
        await self.session.flush()
        if should_make_default:
            await self.set_default(address)
        return address

    async def set_default(self, address: EmailSenderAddress) -> EmailSenderAddress:
        scope = [EmailSenderAddress.institution_id == address.institution_id]
        scope.append(
            EmailSenderAddress.location_id == address.location_id
            if address.location_id
            else EmailSenderAddress.location_id.is_(None)
        )
        await self.session.execute(
            update(EmailSenderAddress).where(*scope).values(is_default=False)
        )
        address.is_default = True
        address.is_active = True
        await self.session.flush()
        return address

    async def deprovision(self, identity: EmailSendingIdentity) -> None:
        records = [DnsRecord(**r) for r in (identity.dns_records or [])]
        records += [DnsRecord(**r) for r in (identity.inbound_dns_records or [])]
        shared_provider_resources = (
            await self.session.execute(
                select(EmailSendingIdentity.id)
                .where(
                    EmailSendingIdentity.institution_id == identity.institution_id,
                    EmailSendingIdentity.id != identity.id,
                    EmailSendingIdentity.provider_tenant_name
                    == identity.provider_tenant_name,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        await self.provisioning.teardown(
            domain=identity.domain,
            inbound_domain=identity.inbound_domain,
            dns_records=records or None,
            tenant_name=(
                None if shared_provider_resources else identity.provider_tenant_name
            ),
            configuration_set=(
                None
                if shared_provider_resources
                else identity.provider_configuration_set
            ),
            manage_dns=bool(identity.dns_managed),
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
            identity.is_active = False
            identity.failure_reason = detail or f"Provider reported {status}"

        else:  # PENDING / TEMPORARY_FAILURE
            if identity.verified_at:
                # It used to verify and no longer does — almost always the DNS
                # records were removed. Distinct from never having verified,
                # because mail *was* flowing and is now failing authentication.
                identity.status = EmailIdentityStatus.REVOKED.value
                identity.is_active = False
                identity.failure_reason = (
                    detail
                    or "Domain no longer verifies; DNS records may have been removed"
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


def _provider_enabled(identity: EmailSendingIdentity) -> bool:
    """Deployment interlock for a verified/active provider identity."""
    return identity.provider != "ses" or settings.ses_clinic_sending_enabled
