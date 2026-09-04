"""Durable record of a status transition on a record this platform owns.

The row is the message. A trigger task re-reads it by id rather than trusting a
Celery payload, which is the same discipline
:class:`~src.app.models.patient_workflow_status.PatientWorkflowStatusEvent`
already uses — and it means a status change survives a worker dying between the
commit and the enqueue.

Written by the session listener in
``src.app.services.automation.internal_status_events``, never by hand: the point
of the listener is that a new write site cannot forget to emit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base

#: Status fields the platform owns and can start a campaign from.
#:
#: Kept to fields with genuine transitions. ``calls.call_status`` (the AI
#: classification) and ``calls.patient_status`` are written once when the call
#: record is created and never updated, so "changed" has no meaning for them; a
#: campaign reacting to an AI disposition subscribes to
#: ``call.inbound.completed`` and filters on ``call.outcome`` instead.
INTERNAL_STATUS_FIELDS: tuple[str, ...] = (
    "call_workflow_status",
    "contact_lead_status",
    "handoff_status",
    "patient_workflow_status",
)


class InternalStatusEvent(Base):
    """One observed transition of a tracked status field."""

    __tablename__ = "internal_status_events"
    __table_args__ = (
        Index(
            "ix_internal_status_events_match",
            "institution_id",
            "field",
            "to_status",
            "created_at",
        ),
        Index(
            "ix_internal_status_events_contact",
            "institution_id",
            "contact_id",
            "created_at",
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    #: One of INTERNAL_STATUS_FIELDS.
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Which row changed, so a campaign can be traced back to its cause. Not a
    #: foreign key: the referent differs per field (a call, a contact, a
    #: handoff), and the event outliving a deleted row is fine.
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: NULL when the record had no prior value, which is a real transition and
    #: distinct from "we did not observe the old value".
    from_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    to_status: Mapped[str] = mapped_column(String(80), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
