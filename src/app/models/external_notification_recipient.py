"""External notification recipients configured per institution.

Allows institution admins to add email addresses for people not on the
platform who should receive specific notification types.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class ExternalNotificationRecipient(Base):
    """An external email address that receives notification emails for an institution."""

    __tablename__ = "external_notification_recipients"
    __table_args__ = (
        Index(
            "ix_ext_recipient_institution_email_type",
            "institution_id",
            "email",
            "template_type",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ExternalNotificationRecipient(id={self.id}, "
            f"email={self.email}, type={self.template_type})>"
        )
