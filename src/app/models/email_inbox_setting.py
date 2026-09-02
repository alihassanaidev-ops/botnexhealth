"""Tenant-controlled inbound email settings.

One row may describe an institution default or a location override.  The
platform owns the SES receiving pipeline; clinics control whether their signed
address accepts new conversations and what automation should do after a reply.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class EmailInboxSetting(Base):
    __tablename__ = "email_inbox_settings"
    __table_args__ = (
        Index(
            "uq_email_inbox_settings_institution_default",
            "institution_id",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
        ),
        Index(
            "uq_email_inbox_settings_location",
            "location_id",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    allow_new_contacts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    stop_automation_on_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    forward_to_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def forward_to(self) -> str | None:
        return decrypt_value(self.forward_to_encrypted)

    @forward_to.setter
    def forward_to(self, value: str | None) -> None:
        self.forward_to_encrypted = encrypt_value(value)

