"""Clinic-owned sender addresses attached to a verified email domain."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class EmailSenderAddress(Base):
    """One selectable From address for an institution or one of its locations.

    The parent ``EmailSendingIdentity`` owns provider verification and DNS. Many
    addresses may safely share it; deleting an address therefore never deletes
    the SES domain identity.
    """

    __tablename__ = "email_sender_addresses"
    __table_args__ = (
        Index("ix_email_sender_addresses_institution", "institution_id"),
        Index("ix_email_sender_addresses_domain", "email_identity_id"),
        CheckConstraint(
            "local_part ~ '^[a-z0-9._-]+$'",
            name="ck_email_sender_addresses_local_part",
        ),
        Index(
            "uq_email_sender_addresses_institution_default",
            "institution_id",
            unique=True,
            postgresql_where=text("location_id IS NULL AND is_default"),
        ),
        Index(
            "uq_email_sender_addresses_location_default",
            "institution_id",
            "location_id",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL AND is_default"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    email_identity_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("email_sending_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=True,
    )
    local_part: Mapped[str] = mapped_column(String(64), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
