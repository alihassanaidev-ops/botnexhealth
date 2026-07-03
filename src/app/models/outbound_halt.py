"""OutboundEmergencyHalt — institution-level kill switch for outbound campaigns."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class OutboundEmergencyHalt(Base):
    """Active halt record for an institution's outbound campaigns.

    One active row (released_at IS NULL) means all outbound sends are blocked
    for that institution. Released when released_at is set. Append-only for
    audit trail — never delete rows.
    """

    __tablename__ = "outbound_emergency_halts"
    __table_args__ = (
        Index(
            "ix_outbound_emergency_halts_institution_active",
            "institution_id",
            postgresql_where="released_at IS NULL",
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
    halted_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        active = "active" if self.released_at is None else "released"
        return f"<OutboundEmergencyHalt(institution_id={self.institution_id}, {active})>"
