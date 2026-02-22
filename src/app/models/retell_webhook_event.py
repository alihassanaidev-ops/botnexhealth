"""Idempotency model for Retell webhook event processing."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class RetellWebhookStatus(str, Enum):
    """Lifecycle status of a Retell webhook processing record."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RetellWebhookEvent(Base):
    """
    Tracks webhook processing state by (call_id, event_type) for idempotency.

    This table prevents duplicate side effects when Retell retries webhooks.
    """

    __tablename__ = "retell_webhook_events"
    __table_args__ = (
        UniqueConstraint("call_id", "event_type", name="uq_retell_webhook_call_event"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RetellWebhookStatus.PROCESSING.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<RetellWebhookEvent(call_id={self.call_id}, event_type={self.event_type}, "
            f"status={self.status}, attempts={self.attempts})>"
        )

