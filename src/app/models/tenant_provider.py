"""TenantProvider model — cached PMS provider data per location."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class TenantProvider(Base):
    """
    Locally cached provider data synced from PMS (NexHealth/Sikka).

    No PHI stored — only provider name, specialty, and source IDs.
    """

    __tablename__ = "tenant_providers"

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

    source: Mapped[str] = mapped_column(String(50), nullable=False)  # "nexhealth" | "sikka"
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TenantProvider(id={self.id}, name='{self.name}', source={self.source})>"
