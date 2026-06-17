"""InstitutionGroup model — a DSO / practice-group umbrella over institutions.

A group owns N institutions (each still a fully isolated tenant). A GROUP_ADMIN
user is scoped to one group and gets read-only, aggregate oversight across its
member institutions' dashboards — no PHI, no writes. The group is purely an
oversight layer; it holds no patient data itself.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class InstitutionGroup(Base):
    """A group/DSO that owns multiple institutions for oversight purposes."""

    __tablename__ = "institution_groups"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<InstitutionGroup(id={self.id}, slug='{self.slug}')>"
