"""Per-user email notification preferences.

Uses an opt-out model: absence of a row means the user receives all
notifications. Rows are only created when a user explicitly disables
a notification type.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class UserEmailNotificationPreference(Base):
    """A user's opt-in/opt-out preference for a specific email notification type."""

    __tablename__ = "user_email_notification_preferences"
    __table_args__ = (
        Index(
            "ix_user_email_pref_user_type",
            "user_id",
            "template_type",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<UserEmailNotificationPreference(user_id={self.user_id}, "
            f"type={self.template_type}, enabled={self.is_enabled})>"
        )
