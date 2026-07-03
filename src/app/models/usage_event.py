"""Usage-metering ingestion model (Plan 11 core).

Captures per-interaction consumption (SMS segments, email sends, voice minutes)
so downstream cost rollup and analytics have a durable data source. Rows are
append-style billing signals; an ``idempotency_key`` (unique per institution)
ensures repeated webhook deliveries do not double-count.

This model intentionally records only non-PHI billing metadata — quantities,
provider cost, and provider message references — never message bodies.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class UsageChannel(str, Enum):
    SMS = "sms"
    EMAIL = "email"
    VOICE = "voice"


class UsageDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class UsageProvider(str, Enum):
    TWILIO = "twilio"
    RESEND = "resend"
    RETELL = "retell"


class UsageEvent(Base):
    """A single metered consumption signal for one channel interaction."""

    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('sms', 'email', 'voice')",
            name="ck_usage_events_channel",
        ),
        CheckConstraint(
            "direction IN ('outbound', 'inbound')",
            name="ck_usage_events_direction",
        ),
        CheckConstraint(
            "provider IN ('twilio', 'resend', 'retell')",
            name="ck_usage_events_provider",
        ),
        Index("ix_usage_events_institution_occurred", "institution_id", "occurred_at"),
        Index("ix_usage_events_channel", "channel"),
        Index(
            "uq_usage_events_idempotency",
            "institution_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(20),
        default=UsageDirection.OUTBOUND.value,
        server_default=text("'outbound'"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)

    # Quantity fields — only the ones relevant to the channel are populated.
    segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    dials: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emails: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provider-reported cost. Twilio prices carry up to 5 decimal places.
    cost_amount: Mapped[float | None] = mapped_column(Numeric(12, 5), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        server_default=text("'USD'"),
        nullable=False,
    )

    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<UsageEvent(id={self.id}, channel={self.channel}, "
            f"provider={self.provider}, occurred_at={self.occurred_at})>"
        )
