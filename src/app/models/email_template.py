"""Email template model for customizable notification emails.

Each institution can have custom email templates for different notification types.
Templates use Jinja2 syntax for variable substitution.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class EmailTemplateType(str, Enum):
    """Types of email templates that map to notification events."""

    CALL_SUMMARY = "call_summary"
    URGENT_ALERT = "urgent_alert"
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"


class EmailTemplate(Base):
    """
    A customizable email template scoped to an institution.

    Each institution gets default templates on first access, which can then
    be edited via the dashboard. Templates use Jinja2 syntax for variable
    substitution (e.g. {{ location_name }}, {{ summary }}).
    """

    __tablename__ = "email_templates"
    __table_args__ = (
        Index("ix_email_template_institution_type", "institution_id", "template_type", unique=True),
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
    )

    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_template: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
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
            f"<EmailTemplate(id={self.id}, type={self.template_type}, "
            f"name={self.name}, active={self.is_active})>"
        )
