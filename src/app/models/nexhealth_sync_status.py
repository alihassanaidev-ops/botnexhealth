"""Latest NexHealth PMS sync health per configured clinic location."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class NexHealthSyncStatus(Base):
    """Operational PMS read/write sync state used by campaign readiness."""

    __tablename__ = "nexhealth_sync_statuses"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "location_id",
            name="uq_nexhealth_sync_status_location",
        ),
        Index("ix_nexhealth_sync_statuses_checked", "institution_id", "last_checked_at"),
        Index("ix_nexhealth_sync_statuses_read", "institution_id", "read_status"),
        Index("ix_nexhealth_sync_statuses_write", "institution_id", "write_status"),
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
    sync_source_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sync_source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    emr_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    locations_payload: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    read_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    write_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    write_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
