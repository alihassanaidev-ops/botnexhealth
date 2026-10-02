"""A credential a clinic hands to a form provider so it can land leads.

One row per form, not per clinic, so a practice can run several — a website
enquiry form, a Typeform campaign landing page, a paid-ads form — and revoke one
without taking the others down with it. The label is what the clinic recognises
it by later, when deciding which of them to turn off.

The token is stored as a keyed hash. It is a bearer credential: whoever holds it
can create enquiries in this clinic's scope, so it gets the treatment a password
gets rather than the treatment an id gets. It is shown once, at creation, and
cannot be recovered afterwards — only replaced.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value
from src.app.security import keyed_hash

#: Purpose-scoped so an intake token hash can never collide with, or be
#: substituted for, a hash of the same string made for another purpose.
TOKEN_PURPOSE = "enquiry-intake-token-v1"


def generate_intake_token() -> str:
    """A fresh token. URL-safe because it travels in the endpoint's path."""
    return secrets.token_urlsafe(32)


def hash_intake_token(token: str | None) -> str | None:
    if not token:
        return None
    return keyed_hash(token, purpose=TOKEN_PURPOSE)


class EnquiryIntakeSource(Base):
    __tablename__ = "enquiry_intake_sources"
    __table_args__ = (
        # The token is what identifies the institution, so it cannot itself be
        # scoped by one — uniqueness has to be platform wide.
        UniqueConstraint("token_hash", name="uq_enquiry_intake_sources_token_hash"),
        Index("ix_enquiry_intake_sources_institution", "institution_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Which location the leads belong to. Null means the institution decides
    #: later — useful for a single-location practice or a group-level form.
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=True,
    )

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Optional. When set, the request body must carry a matching HMAC — which
    #: proves the body was not altered in transit, something a bearer token in a
    #: URL cannot do on its own.
    signing_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Recorded on every enquiry from this source, so a clinic can tell which
    #: form produced which lead without inspecting attribution.
    source_name: Mapped[str] = mapped_column(
        String(80), nullable=False, default="external_form"
    )
    #: Merged under whatever the request supplies, so a form that knows nothing
    #: about UTM still lands attributed.
    default_attribution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    #: Lets a clinic see a form has gone quiet, which is usually how a broken
    #: integration is noticed at all.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def signing_secret(self) -> str | None:
        return decrypt_value(self.signing_secret_encrypted)

    @signing_secret.setter
    def signing_secret(self, value: str | None) -> None:
        self.signing_secret_encrypted = encrypt_value(value)
