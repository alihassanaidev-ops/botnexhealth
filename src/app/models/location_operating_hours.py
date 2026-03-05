"""LocationOperatingHours — per-day open/close schedule for a clinic location."""

from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class LocationOperatingHours(Base):
    """
    Defines when a clinic location is open, one row per day of week.

    day_of_week uses ISO convention: 0 = Monday … 6 = Sunday.
    If no rows exist for a location, the feature is considered
    unconfigured and all NexHealth slots pass through unfiltered.
    """

    __tablename__ = "location_operating_hours"
    __table_args__ = (
        UniqueConstraint("location_id", "day_of_week", name="uq_location_day"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon … 6=Sun
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)   # e.g. 08:00
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # e.g. 17:00

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<LocationOperatingHours(location_id={self.location_id}, "
            f"day={self.day_of_week}, open={self.is_open}, "
            f"{self.open_time}-{self.close_time})>"
        )
