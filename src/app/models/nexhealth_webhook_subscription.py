"""NexHealth webhook subscription lifecycle state (Plan 09).

One row tracks the expected appointment-webhook subscription for one configured
NexHealth location. NexHealth remains the system of record for appointments; this
table is operational state: setup, health, last event, and backfill/reconcile
watermarks.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class NexHealthWebhookSubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"


class NexHealthWebhookSubscription(Base):
    """Local lifecycle/health row for a NexHealth appointment webhook subscription."""

    __tablename__ = "nexhealth_webhook_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "location_id",
            name="uq_nexhealth_webhook_subscription_location",
        ),
        Index(
            "ix_nexhealth_webhook_subscriptions_status",
            "institution_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subdomain: Mapped[str] = mapped_column(String(160), nullable=False)
    nexhealth_location_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NexHealthWebhookSubscriptionStatus.PENDING.value,
        server_default=text("'pending'"),
    )

    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_backfill_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_patient_backfill_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_patient_reconciliation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
