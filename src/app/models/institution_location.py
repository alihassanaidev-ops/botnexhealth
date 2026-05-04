"""InstitutionLocation model — physical practice within an institution."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class InstitutionLocation(Base):
    """
    A physical practice location within an institution.

    Institution = dental group, InstitutionLocation = individual office.
    Each location can have its own NexHealth subdomain/location_id,
    Retell agent, and address info.
    """

    __tablename__ = "institution_locations"
    __table_args__ = (
        # Slugs are unique WITHIN an institution, not globally — two different
        # dental groups can each have a location named "main" or "downtown".
        UniqueConstraint(
            "institution_id", "slug", name="uq_institution_locations_inst_slug"
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
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # NexHealth — location-level overrides
    nexhealth_subdomain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nexhealth_location_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # Retell — per-location agent
    retell_agent_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )

    # Twilio — outbound SMS number for this location (E.164, e.g. +12125551234)
    twilio_from_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Configuration
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)

    # Address
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # =========================================================================
    # Methods
    # =========================================================================

    def __repr__(self) -> str:
        return f"<InstitutionLocation(id={self.id}, institution_id={self.institution_id}, name='{self.name}')>"
