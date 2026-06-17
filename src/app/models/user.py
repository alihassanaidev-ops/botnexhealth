"""
User model for authentication and role management.

The application owns User IDs. Auth provider integration must not be the
source of truth for primary keys in our database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
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
    # Read-only oversight across an InstitutionGroup's member practices.
    # Has group_id set; institution_id/location_id are NULL. Granted only on
    # the /group/* endpoints — never on PHI/setup/call routes.
    GROUP_ADMIN = "GROUP_ADMIN"


class InviteStatus(str, Enum):
    """Invite acceptance status."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class User(Base):
    """
    User model for authentication.

    Fields:
    - id: Application-owned UUID
    - email: User's email address (login credential)
    - role: User role (SUPER_ADMIN, INSTITUTION_ADMIN, LOCATION_ADMIN, STAFF)
    - institution_id: Institution this user belongs to (nullable for SUPER_ADMIN)
    - location_id: Location this user is scoped to (nullable for INSTITUTION_ADMIN)
    - password_hash: Argon2id password hash for local authentication
    - invite_token_hash/password_reset_token_hash: hashed one-time tokens
    - is_active: Whether the user can log in
    - failed_login_attempts: Consecutive failed login attempts since last success
    - locked_until: Account locked until this UTC timestamp (None = not locked)
    """

    __tablename__ = "users"
    __table_args__ = (
        # Email is unique among ACTIVE users only. Soft-deleted users
        # (deleted_at IS NOT NULL) are preserved indefinitely for audit-log
        # FK integrity (HIPAA §164.530(j)), so re-onboarding the same email
        # after soft-delete must succeed. Every caller that checks "does
        # this email already exist?" MUST filter deleted_at IS NULL to
        # match this guarantee.
        Index(
            "users_email_active_uq",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
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

    # Set only for GROUP_ADMIN users — the InstitutionGroup they oversee.
    # Mutually exclusive with institution_id/location_id in practice.
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("institution_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    invite_status: Mapped[str] = mapped_column(
        String(20),
        default=InviteStatus.PENDING.value,
        nullable=False,
        server_default="PENDING",
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    password_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    invite_token_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    password_reset_token_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
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

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
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

    def mark_deleted(self) -> None:
        """Soft-delete a user account and revoke interactive access."""
        self.is_active = False
        self.deleted_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"
