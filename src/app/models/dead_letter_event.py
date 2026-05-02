"""Dead-letter event model for failed webhook/task handling."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class DeadLetterStatus(str, Enum):
    OPEN = "open"
    REPLAYED = "replayed"
    DISCARDED = "discarded"


class DeadLetterEvent(Base):
    """Captured failed event with redacted payload and encrypted replay payload."""

    __tablename__ = "dead_letter_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DeadLetterStatus.OPEN.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    redacted_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
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
