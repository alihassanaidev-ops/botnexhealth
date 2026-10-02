"""Clinic-authored campaign email templates.

Separate from :class:`~src.app.models.email_template.EmailTemplate`, which holds
the five fixed *system* notification templates keyed by ``EmailTemplateType``
(call summary, urgent alert, …). Those are bound to specific call events and
render a call-centric variable set (``caller_phone``, ``duration``,
``dashboard_link``).

These are free-form: a clinic creates as many as it likes — "Post-Op Day 1",
"Recall Reminder", "Welcome" — names them itself, and references them from a
``send_email`` workflow node by ``key``. They render the campaign merge-field
catalog (``patient_first_name``, ``clinic_name``, …) instead.

Kept as its own table rather than a ``scope`` column on ``email_templates``
because the two have different key spaces (closed enum vs free slug), different
variable vocabularies, and because widening the existing
``(institution_id, template_type)`` unique index would mean touching the working
notification path for no gain.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base

#: Keys are referenced from workflow definitions, so they must stay URL- and
#: JSON-safe and stable. Mirrored by a CHECK constraint in the migration.
TEMPLATE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")

MAX_TEMPLATES_PER_INSTITUTION = 200


def slugify_template_key(value: str) -> str:
    """Best-effort conversion of a display name into a valid key."""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:80]


class CampaignEmailTemplate(Base):
    """A named, reusable email template owned by one institution."""

    __tablename__ = "campaign_email_templates"
    __table_args__ = (
        Index(
            "ix_campaign_email_template_institution_key",
            "institution_id",
            "key",
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

    #: Stable identifier referenced from workflow definitions.
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    subject_template: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Publishing a workflow that references an inactive template is rejected,
    #: so deactivating is a soft delete that keeps history readable.
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
            f"<CampaignEmailTemplate(id={self.id}, key={self.key}, "
            f"active={self.is_active})>"
        )
