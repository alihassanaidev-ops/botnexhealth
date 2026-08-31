"""Managing quiet-hours exceptions, and refusing the ones that lock a clinic out.

The dangerous exception is not the wrong one, it is the one that leaves no
permitted window at all. Every send then holds, waiting for a window that never
opens, and the campaign goes quiet without a single error: the engine is
behaving exactly as designed. The scope note is explicit that this must be
caught *when the exception is saved*, with an explanation, rather than when a
message fails to send.

Validation therefore runs the real evaluator rather than re-deriving the rule.
The proposed row is flushed into the transaction, ``next_permitted_window`` is
asked whether any window survives, and the caller rolls back if not. Reasoning
about it independently would work today and drift the first time precedence
changes — and this is precisely the check that must not quietly stop matching
what the engine does.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.quiet_hours_exception import QuietHoursException
from src.app.services.automation.quiet_hours_service import QuietHoursService

logger = logging.getLogger(__name__)

__all__ = ["QuietHoursExceptionError", "QuietHoursExceptionService"]


class QuietHoursExceptionError(ValueError):
    """A proposed exception was rejected. The message is shown to the operator."""


class QuietHoursExceptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_location(
        self, institution_id: str, location_id: str
    ) -> list[QuietHoursException]:
        result = await self.session.execute(
            select(QuietHoursException)
            .where(
                QuietHoursException.institution_id == institution_id,
                QuietHoursException.location_id == location_id,
            )
            .order_by(QuietHoursException.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        institution_id: str,
        location_id: str,
        contact_id: str | None = None,
        exception_date: date_type | None = None,
        content_class: str | None = None,
        is_blocked: bool = False,
        open_time: time | None = None,
        close_time: time | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> QuietHoursException:
        """Add an exception, or raise ``QuietHoursExceptionError`` explaining why not."""
        exception = QuietHoursException(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            exception_date=exception_date,
            content_class=content_class,
            is_blocked=is_blocked,
            open_time=open_time,
            close_time=close_time,
            reason=reason,
        )
        self._check_window_shape(exception)
        self.session.add(exception)
        await self.session.flush()
        await self._check_leaves_a_window(exception, now=now)
        return exception

    async def update(
        self, exception: QuietHoursException, *, now: datetime | None = None, **fields
    ) -> QuietHoursException:
        """Apply *fields*, validating the result exactly as a create would."""
        for name, value in fields.items():
            if not hasattr(exception, name):
                raise QuietHoursExceptionError(f"Unknown field '{name}'.")
            setattr(exception, name, value)
        self._check_window_shape(exception)
        await self.session.flush()
        await self._check_leaves_a_window(exception, now=now)
        return exception

    async def delete(self, exception: QuietHoursException) -> None:
        """Removing an exception only ever widens the window, so it needs no check."""
        await self.session.delete(exception)
        await self.session.flush()

    # ── validation ──────────────────────────────────────────────────────

    @staticmethod
    def _check_window_shape(exception: QuietHoursException) -> None:
        """Reject a window that is empty on its face, before touching the database."""
        if exception.is_blocked:
            return
        open_at = exception.open_time
        close_at = exception.close_time
        if open_at is not None and close_at is not None and open_at >= close_at:
            raise QuietHoursExceptionError(
                f"This exception opens at {open_at.strftime('%H:%M')} and closes at "
                f"{close_at.strftime('%H:%M')}, so it permits no time at all. Set a "
                "closing time later than the opening time, or mark it as blocked if "
                "the intention is to prevent contact entirely."
            )

    async def _check_leaves_a_window(
        self, exception: QuietHoursException, *, now: datetime | None = None
    ) -> None:
        """Refuse an exception that leaves its audience with no window at all.

        Asked of the audience the exception actually affects: a rule naming a
        patient is checked for that patient, and one naming a content class for
        that class. Checking only the location's default would pass a patient
        rule that silences that patient for ever.
        """
        evaluator = QuietHoursService(self.session)
        window = await evaluator.next_permitted_window(
            exception.location_id,
            now=now or datetime.now(tz=timezone.utc),
            contact_id=exception.contact_id,
            content_class=exception.content_class,
        )
        if window is not None:
            return

        raise QuietHoursExceptionError(
            f"{self._audience(exception)} would have no permitted contact window "
            "within the next week, so every message would be held indefinitely and "
            "nothing would ever send. Narrow the exception — give it a date, or a "
            "window rather than a full block — or adjust the location's opening "
            "hours first."
        )

    @staticmethod
    def _audience(exception: QuietHoursException) -> str:
        parts = []
        if exception.contact_id is not None:
            parts.append("This patient")
        else:
            parts.append("This location")
        if exception.content_class:
            parts.append(f"for {exception.content_class} messages")
        if exception.exception_date is not None:
            parts.append(f"on {exception.exception_date.isoformat()}")
        return " ".join(parts)
