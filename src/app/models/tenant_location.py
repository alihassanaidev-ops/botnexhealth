"""TenantLocation model — physical practice within a tenant (institution)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.tenant import decrypt_value, encrypt_value


class TenantLocation(Base):
    """
    A physical practice location within a tenant institution.

    Tenant = institution/dental group, TenantLocation = individual office.
    Each location can have its own NexHealth subdomain/location_id,
    Retell agent, and address info.
    """

    __tablename__ = "tenant_locations"

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

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # NexHealth — location-level overrides
    nexhealth_subdomain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nexhealth_location_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Retell — per-location agent
    retell_agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    retell_api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


    # Address
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Encrypted field properties
    @property
    def retell_api_secret(self) -> str | None:
        return decrypt_value(self.retell_api_secret_encrypted)

    @retell_api_secret.setter
    def retell_api_secret(self, value: str | None) -> None:
        self.retell_api_secret_encrypted = encrypt_value(value)

    def __repr__(self) -> str:
        return f"<TenantLocation(id={self.id}, name='{self.name}', slug='{self.slug}')>"
