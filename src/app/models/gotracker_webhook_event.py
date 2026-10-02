"""GoTracker webhook event ledger."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class GoTrackerWebhookStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GoTrackerWebhookEvent(Base):
    """One inbound GoTracker webhook event (idempotency claim + audit)."""

    __tablename__ = "gotracker_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "dedup_key",
            name="uq_gotracker_webhook_events_dedup",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    gotracker_appointment_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    gotracker_patient_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(300), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=GoTrackerWebhookStatus.PROCESSING.value,
        server_default=text("'PROCESSING'"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
        if not isinstance(payload, dict):
            return {"payload": "[redacted]"}
        return payload

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
