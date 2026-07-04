"""NexHealth webhook event ledger (Plan 09 D-4).

Event-level idempotency + audit for inbound NexHealth appointment webhooks.
Mirrors ``retell_webhook_event``: a unique ``dedup_key`` acts as the processing
claim, so a redelivered event is recognised at receipt instead of re-running the
trigger and being deduped only downstream at ``enroll()``.

``dedup_key`` is the *semantic* identity of the event — a redelivery of the same
logical change collides; a genuine reschedule (new start_time) does not.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class NexHealthWebhookStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NexHealthWebhookEvent(Base):
    """One inbound NexHealth appointment webhook event (idempotency claim + audit)."""

    __tablename__ = "nexhealth_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "dedup_key",
            name="uq_nexhealth_webhook_events_dedup",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    nexhealth_appointment_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Semantic identity: "{event}:{appt_id}:{start_or_cancelled}".
    dedup_key: Mapped[str] = mapped_column(String(300), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NexHealthWebhookStatus.PROCESSING.value,
        server_default=text("'PROCESSING'"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
