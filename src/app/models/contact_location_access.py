"""Contact-to-location visibility grants for RLS."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class ContactLocationAccess(Base):
    """A location-scoped user may see a contact through this grant."""

    __tablename__ = "contact_location_accesses"
    __table_args__ = (
        UniqueConstraint(
            "contact_id",
            "location_id",
            name="uq_contact_location_access_contact_location",
        ),
        Index("ix_contact_location_access_institution", "institution_id"),
        Index("ix_contact_location_access_location", "location_id"),
        Index("ix_contact_location_access_contact", "contact_id"),
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
    contact_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
