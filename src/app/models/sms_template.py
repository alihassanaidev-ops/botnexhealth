"""SMS template model for customizable patient text notifications.

Mirrors :mod:`src.app.models.email_template` but for SMS: each institution can
customize the body of the transactional texts we send to patients. Templates
use Jinja2 syntax for variable substitution (e.g. {{ patient_name }},
{{ appointment_provider }}). The clinic-identity prefix and the CASL/TCPA
opt-out footer are applied downstream at send time by ``sms_privacy`` — they
are NOT part of the editable body.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class SmsTemplateType(str, Enum):
    """Types of SMS templates that map to patient-facing notification events."""

    # Confirmation texted to the patient once an appointment is booked on a call.
    APPOINTMENT_BOOKED = "appointment_booked"
    # No-PMS ("none") variant: the AI can't truly book, so the patient is texted
    # that their request was received and the office will confirm. Kept separate
    # from APPOINTMENT_BOOKED so integrated confirmation SMS is untouched.
    APPOINTMENT_REQUEST = "appointment_request"


class SmsTemplate(Base):
    """A customizable SMS template scoped to an institution.

    Each institution gets default templates on first access, which can then be
    edited via the dashboard. Unlike email there is no subject/HTML — an SMS is
    a single plain-text body rendered with Jinja2.
    """

    __tablename__ = "sms_templates"
    __table_args__ = (
        Index(
            "ix_sms_template_institution_type",
            "institution_id",
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
    )

    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
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
            f"<SmsTemplate(id={self.id}, type={self.template_type}, "
            f"name={self.name}, active={self.is_active})>"
        )
