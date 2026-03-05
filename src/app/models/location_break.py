"""LocationBreak — recurring break windows (e.g. lunch) for a clinic location."""

from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class LocationBreak(Base):
    """
    A recurring break period for a clinic location.

    If day_of_week is NULL the break applies every day the clinic is open.
    Otherwise it applies only to the specified day (0=Monday … 6=Sunday).
    """

    __tablename__ = "location_breaks"

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

    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Lunch Break"
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = every day
    start_time: Mapped[time] = mapped_column(Time, nullable=False)  # e.g. 12:00
    end_time: Mapped[time] = mapped_column(Time, nullable=False)    # e.g. 13:00

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        day = f"day={self.day_of_week}" if self.day_of_week is not None else "every_day"
        return (
            f"<LocationBreak(location_id={self.location_id}, "
            f"name='{self.name}', {day}, {self.start_time}-{self.end_time})>"
        )
