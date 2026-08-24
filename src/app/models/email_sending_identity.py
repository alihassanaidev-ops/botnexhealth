"""Per-clinic email sending identity and its verification state.

A clinic's patients should see mail from the clinic, not from the platform. That
needs more than a from-address field: an address only delivers if the domain it
belongs to is authenticated (DKIM/SPF/DMARC) with the sending provider. An
unverified domain does not bounce loudly — it lands in spam — so the verification
state has to be modelled and enforced rather than assumed.

Rows are scoped either to a whole institution (``location_id IS NULL``) or to a
single location. Resolution prefers the location row, then the institution row,
then the platform default. That mirrors how SMS already resolves a sender, where
each location carries its own ``twilio_from_number``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class EmailIdentityStatus(str, Enum):
    """Lifecycle of a sending domain.

    ``PENDING_DNS`` — created with the provider; DNS records not published yet.
    ``VERIFYING``   — records published; waiting for the provider to confirm.
    ``VERIFIED``    — authenticated and safe to send from.
    ``FAILED``      — verification did not complete in time.
    ``REVOKED``     — was verified, then stopped verifying. Usually the DNS
                      records were removed. Distinct from FAILED because mail
                      *was* flowing and has now silently started failing
                      authentication, which is the more urgent case.
    """

    PENDING_DNS = "pending_dns"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    REVOKED = "revoked"


#: Statuses from which sending is refused.
UNSENDABLE_STATUSES = frozenset(
    {
        EmailIdentityStatus.PENDING_DNS.value,
        EmailIdentityStatus.VERIFYING.value,
        EmailIdentityStatus.FAILED.value,
        EmailIdentityStatus.REVOKED.value,
    }
)


class EmailSendingIdentity(Base):
    """The address a clinic's patient email is sent from."""

    __tablename__ = "email_sending_identities"
    __table_args__ = (
        Index(
            "ix_email_sending_identity_scope",
            "institution_id",
            "location_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: NULL means the institution-wide default.
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="ses")
    #: The authenticated domain, e.g. "brightsmile.mail.scalenexus.ai".
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Where patient replies should go while inbound is not yet built — usually
    #: the clinic's real mailbox.
    reply_to_address: Mapped[str | None] = mapped_column(String(320), nullable=True)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=EmailIdentityStatus.PENDING_DNS.value
    )
    #: DNS records the provider requires, as returned at creation. Kept so the
    #: dashboard can show them for a clinic-owned domain the platform cannot
    #: publish into itself.
    dns_records: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Provider-side references — the SES tenant and configuration set bound to
    #: this clinic, used to scope reputation and bounce handling per tenant.
    provider_tenant_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_configuration_set: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def is_sendable(self) -> bool:
        return self.status == EmailIdentityStatus.VERIFIED.value

    def __repr__(self) -> str:
        return (
            f"<EmailSendingIdentity(domain={self.domain}, "
            f"status={self.status}, location_id={self.location_id})>"
        )
