"""NexHealth v3 shadow webhook capture and subscription state."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class NexHealthWebhookShadowParseStatus(str, Enum):
    PARSED = "parsed"
    FAILED = "failed"


class NexHealthWebhookShadowSubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"


class NexHealthWebhookShadowEvent(Base):
    """One captured v3 shadow webhook delivery.

    This table is intentionally separate from ``nexhealth_webhook_events``. The
    live ledger drives workflow idempotency and retry accounting; shadow rows are
    validation evidence only.
    """

    __tablename__ = "nexhealth_webhook_shadow_events"
    __table_args__ = (
        Index(
            "ix_nexhealth_webhook_shadow_events_parse_status",
            "parse_status",
            "created_at",
        ),
        Index(
            "ix_nexhealth_webhook_shadow_events_resource",
            "resource_type",
            "event_name",
        ),
        Index(
            "ix_nexhealth_webhook_shadow_events_resolution",
            "institution_id",
            "location_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    api_contract: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stable_v3", server_default=text("'stable_v3'")
    )
    route_family: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subdomain: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    nexhealth_location_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    event_family: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pms_resource_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    change_marker: Mapped[str | None] = mapped_column(String(300), nullable=True)
    business_event_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )

    provider_delivery_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NexHealthWebhookShadowParseStatus.PARSED.value,
        server_default=text("'parsed'"),
        index=True,
    )
    parse_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unresolved", server_default=text("'unresolved'")
    )
    resolution_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    extracted_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    redacted_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_retain_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    raw_payload_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def redacted_payload(self) -> dict[str, Any] | None:
        text = decrypt_value(self.redacted_payload_encrypted)
        if not text:
            return None
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"payload": "[redacted]"}

    @redacted_payload.setter
    def redacted_payload(self, value: dict[str, Any] | None) -> None:
        if value is None:
            self.redacted_payload_encrypted = None
            return
        text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        self.redacted_payload_encrypted = encrypt_value(text)

    @property
    def raw_payload(self) -> str | None:
        return decrypt_value(self.raw_payload_encrypted)

    @raw_payload.setter
    def raw_payload(self, value: str | None) -> None:
        self.raw_payload_encrypted = encrypt_value(value) if value is not None else None


class NexHealthWebhookShadowSubscription(Base):
    """Local lifecycle state for one v3 shadow route subscription group."""

    __tablename__ = "nexhealth_webhook_shadow_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "location_id",
            "route_family",
            name="uq_nexhealth_webhook_shadow_subscription_route",
        ),
        Index(
            "ix_nexhealth_webhook_shadow_subscriptions_status",
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

    route_family: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    api_contract: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stable_v3", server_default=text("'stable_v3'")
    )
    subdomain: Mapped[str] = mapped_column(String(160), nullable=False)
    nexhealth_location_id: Mapped[str] = mapped_column(String(160), nullable=False)
    callback_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    provider_endpoint_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_subscription_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NexHealthWebhookShadowSubscriptionStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_parse_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_parse_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_shadow_capture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    parse_success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    parse_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
