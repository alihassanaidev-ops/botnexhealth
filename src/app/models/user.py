"""
User model for authentication and role management.

The User.id is the Supabase auth.users.id (UUID) — single source of truth.
It is set explicitly during user creation, not auto-generated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class UserRole(str, Enum):
    """
    User roles for authorization.
    """
    SUPER_ADMIN = "SUPER_ADMIN"
    INSTITUTION_ADMIN = "INSTITUTION_ADMIN"
    LOCATION_ADMIN = "LOCATION_ADMIN"
    STAFF = "STAFF"


class InviteStatus(str, Enum):
    """Invite acceptance status."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class User(Base):
    """
    User model for authentication.

    Fields:
    - id: Supabase auth.users.id (UUID) — set explicitly, not auto-generated
    - email: User's email address (login credential)
    - role: User role (SUPER_ADMIN, INSTITUTION_ADMIN, LOCATION_ADMIN, STAFF)
    - institution_id: Institution this user belongs to (nullable for SUPER_ADMIN)
    - location_id: Location this user is scoped to (nullable for INSTITUTION_ADMIN)
    - is_active: Whether the user can log in
    - failed_login_attempts: Consecutive failed login attempts since last success
    - locked_until: Account locked until this UTC timestamp (None = not locked)
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    role: Mapped[str] = mapped_column(
        String(50),
        default=UserRole.INSTITUTION_ADMIN.value,
        nullable=False
    )

    institution_id: Mapped[str | None] = mapped_column(
        ForeignKey("institutions.id"),
        nullable=True,
        index=True
    )

    location_id: Mapped[str | None] = mapped_column(
        ForeignKey("institution_locations.id"),
        nullable=True,
        index=True
    )

    invite_status: Mapped[str] = mapped_column(
        String(20),
        default=InviteStatus.PENDING.value,
        nullable=False,
        server_default="PENDING",
    )

    invite_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    invite_cooldown_exponent: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
    )

    last_invite_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def is_locked(self) -> bool:
        """Return True if the account lockout is currently active."""
        if self.locked_until is None:
            return False
        return datetime.now(timezone.utc) < self.locked_until

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"
