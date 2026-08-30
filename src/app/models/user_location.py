"""Extra location assignments for location-scoped users.

A LOCATION_ADMIN or STAFF user's primary location lives on
``users.location_id`` (unchanged, and still required for those roles). Rows
here grant the same account access to *additional* locations within the same
institution, so one email can work across several offices and pick one at
login. The set of locations a user may act on is
``{users.location_id} | {user_locations.location_id ...}`` — a
single-location user has no rows here and behaves exactly as before.

``institution_id`` is denormalized from the user so the RLS policy can scope
rows without joining ``users`` (whose own policy would recurse).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class UserLocation(Base):
    """One additional location a location-scoped user may act on."""

    __tablename__ = "user_locations"
    __table_args__ = (
        Index(
            "ix_user_locations_user_location",
            "user_id",
            "location_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    institution_id: Mapped[str] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    location_id: Mapped[str] = mapped_column(
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<UserLocation(user_id={self.user_id}, location_id={self.location_id})>"
