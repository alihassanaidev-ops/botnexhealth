"""Notification model for in-app notification records.

Each notification is scoped to an institution and targeted at a specific user.
Notifications are created when calls are processed, callbacks are resolved, etc.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class NotificationType(str, Enum):
    """Notification categories that map to frontend NotificationType."""

    NEW_CALL = "new_call"
    CALLBACK_ITEM = "callback_item"
    CALLBACK_RESOLVED = "callback_resolved"
    APPOINTMENT_BOOKED = "appointment_booked"
    URGENT = "urgent"


class Notification(Base):
    """
    An in-app notification targeted at a specific user.

    Created during post-call processing or other domain events.
    Each recipient user gets their own row so read-state is per-user.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_unread", "user_id", "is_read"),
        Index("ix_notification_user_created", "user_id", "created_at"),
        Index("ix_notification_institution", "institution_id"),
    )

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    # Institution isolation
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Recipient user
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Notification payload. These values may contain PHI (caller name,
    # summaries, callback details), so they are encrypted at the application
    # layer before being persisted.
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    message_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Extra metadata (call_id, contact identifiers, routing hints, etc.).
    # Stored as encrypted JSON text instead of JSONB because the contents are
    # user-visible notification context, not a query surface.
    data_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def title(self) -> str:
        """Decrypt and return the notification title."""
        return decrypt_value(self.title_encrypted) or ""

    @title.setter
    def title(self, value: str) -> None:
        self.title_encrypted = encrypt_value(value or "")  # type: ignore[assignment]

    @property
    def message(self) -> str:
        """Decrypt and return the notification body."""
        return decrypt_value(self.message_encrypted) or ""

    @message.setter
    def message(self, value: str) -> None:
        self.message_encrypted = encrypt_value(value or "")  # type: ignore[assignment]

    @property
    def data(self) -> dict[str, Any] | None:
        """Decrypt and deserialize the notification metadata payload."""
        raw = decrypt_value(self.data_encrypted)
        if not raw:
            return None
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else None

    @data.setter
    def data(self, value: dict[str, Any] | None) -> None:
        if value is None:
            self.data_encrypted = None
            return
        self.data_encrypted = encrypt_value(
            json.dumps(value, separators=(",", ":"), sort_keys=True)
        )

    def __repr__(self) -> str:
        return (
            f"<Notification(id={self.id}, user_id={self.user_id}, "
            f"type={self.type}, is_read={self.is_read})>"
        )
