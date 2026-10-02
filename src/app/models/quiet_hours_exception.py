"""Quiet-hours exceptions (Item 20).

Quiet hours are derived entirely from a location's weekly opening hours, which
is right most of the time and wrong in three specific ways a clinic cannot
currently express:

  * **A date.** A public holiday when the clinic is shut but its weekly hours
    say open. Today the engine would happily text patients on Christmas Day.
  * **A patient.** Someone who has asked to be contacted only in the evenings.
  * **A kind of message.** A clinic may want a narrower window for marketing
    than for a reminder about tomorrow's appointment — and a reminder for a 7am
    appointment may legitimately need to go out before the doors open.

One table covers all three, because they are the same question — *what is the
permitted window here?* — asked with different amounts of context. A row leaves
unused columns NULL, and NULL means "applies regardless" rather than "no match".


Precedence
----------

More specific wins, scored rather than ordered by hand so the rule stays
readable as columns are added: a patient-level row beats a clinic-level one, a
dated row beats an undated one, and a row naming a content class beats one that
does not. Ties are impossible in practice and broken by the newest row, since
two identical exceptions are a data-entry mistake either way.

Exactly one exception applies, and when none does the weekly hours stand. An
exception fully replaces the day's window rather than intersecting with it —
intersection reads as a safety property but is the wrong behaviour for the
reminder case, where the whole point is to permit a send the weekly hours would
refuse.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class QuietHoursException(Base):
    """One override of a location's default send window."""

    __tablename__ = "quiet_hours_exceptions"
    __table_args__ = (
        Index(
            "ix_quiet_hours_exceptions_location_date",
            "location_id",
            "exception_date",
        ),
        Index(
            "ix_quiet_hours_exceptions_location_contact",
            "location_id",
            "contact_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: NULL applies to every patient at this location.
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: NULL applies on every date; a value applies only on that calendar date,
    #: read in the location's own timezone.
    exception_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: NULL applies to every kind of message. Matches ComplianceMetadata's
    #: content class — transactional_care / recall / sales / marketing.
    content_class: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: True means no contact at all under this exception — the holiday case.
    #: When True the window columns are ignored.
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The permitted window when not blocked. NULL open means midnight, NULL
    #: close means end of day, matching LocationOperatingHours' semantics so the
    #: two sources cannot disagree about what a missing bound means.
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    #: Why this exists. Shown in compliance settings; a holiday nobody can
    #: explain later is a holiday nobody dares delete.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    @property
    def specificity(self) -> int:
        """How closely this row is targeted. Higher wins.

        Weighted so a patient's own preference always outranks a clinic-wide
        rule, whatever else matches: someone who asked not to be called before
        ten should not be called at nine because a marketing exception happens
        to name a content class and their row does not.
        """
        score = 0
        if self.contact_id is not None:
            score += 4
        if self.exception_date is not None:
            score += 2
        if self.content_class is not None:
            score += 1
        return score
