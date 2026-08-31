"""Staff-authored notes on a call record.

A note is free text a human typed about a specific call — "called back, left
voicemail", "booked for Tue 3pm". Staff will inevitably put patient details in
there, so the body is treated as PHI: AES-256-GCM at the application layer on
top of RDS at-rest encryption, same as ``Call.summary``.

Visibility follows the call, not the note. ``institution_id`` and
``location_id`` are copied from the parent call at write time so the RLS policy
can scope a note without joining ``calls`` on every row — a location-scoped
LOCATION_ADMIN/STAFF sees notes only on calls they can already open, and an
INSTITUTION_ADMIN sees every note in their institution.

Deletes are soft (``deleted_at``): a note may be the only record of what a
clinic did about a call, so the row survives for HIPAA §164.312(b) review.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value

#: Cap on a single note body. Long enough for a real handoff paragraph, short
#: enough that the encrypted column stays a reasonable size.
MAX_NOTE_LENGTH = 4000


class CallNote(Base):
    """One note in a call's notes thread."""

    __tablename__ = "call_notes"
    __table_args__ = (
        # The thread read: every live note on one call, oldest first.
        Index("ix_call_notes_call_created", "call_id", "created_at"),
        Index("ix_call_notes_institution", "institution_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    call_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Tenant isolation, denormalized from the parent call so RLS can scope
    # without a join. Kept in step by the write path, never edited after.
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Author. The FK nulls out if the user row is ever hard-deleted, so the
    # email is snapshotted alongside it — the thread must stay attributable.
    author_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Free text typed by staff — assume PHI. See module docstring.
    body_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    #: Set the first time the author edits the body; drives the "edited" marker.
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Soft delete — the row stays for audit; every read filters it out.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    @property
    def body(self) -> str | None:
        return decrypt_value(self.body_encrypted)

    @body.setter
    def body(self, value: str | None) -> None:
        self.body_encrypted = encrypt_value(value)

    def __repr__(self) -> str:
        return (
            f"<CallNote(id={self.id}, call_id={self.call_id}, "
            f"author={self.author_email}, deleted={self.deleted_at is not None})>"
        )
