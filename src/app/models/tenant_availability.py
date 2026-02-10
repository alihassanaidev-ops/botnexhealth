"""TenantAvailability model — cached provider availability schedule per location."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class TenantAvailability(Base):
    """
    Locally cached provider availability (schedule rule) data synced from PMS.

    Stores the provider schedule configuration: days, time range, operatory,
    and linked appointment type IDs. These are schedule *rules*, not individual
    appointment slots.

    No PHI stored — only scheduling metadata.
    """

    __tablename__ = "tenant_availabilities"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenant_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False)  # "nexhealth"
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)

    provider_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operatory_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operatory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    begin_time: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "09:00"
    end_time: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "17:00"
    days: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # ["Monday", "Tuesday", ...]
    specific_date: Mapped[str | None] = mapped_column(String(20), nullable=True)

    appointment_type_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    appointment_type_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<TenantAvailability(id={self.id}, provider='{self.provider_name}', "
            f"time={self.begin_time}-{self.end_time})>"
        )
