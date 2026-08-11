"""Track ScaleNexus-originated GoTracker appointment writes until PMS confirmation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.gotracker_appointment_writeback import (
    GoTrackerAppointmentWriteback,
    GoTrackerAppointmentWritebackAction,
    GoTrackerAppointmentWritebackStatus,
)
from src.app.services.sms_privacy import sanitize_provider_error

WritebackAction = Literal["reschedule", "cancel", "confirm", "status"]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class GoTrackerAppointmentWritebackService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_request(
        self,
        *,
        institution_id: str,
        appointment_id: str,
        location_id: str | None,
        contact_id: str | None = None,
        workflow_run_id: str | None = None,
        step_id: str | None = None,
        action: WritebackAction,
        requested_start_time: str | None = None,
        provider_id: str | None = None,
        status_id: int | None = None,
        confirmed: bool | None = None,
        preconfirmed: bool | None = None,
    ) -> GoTrackerAppointmentWriteback:
        """Persist the mutation we asked GoTracker to apply.

        The writeback completion webhook currently only says "appointment N
        completed/failed", not which action or time. This row supplies that missing
        action context.
        """
        previous_start_time = await self._current_start_time(
            institution_id=institution_id,
            appointment_id=appointment_id,
        )
        row = GoTrackerAppointmentWriteback(
            id=str(uuid4()),
            institution_id=institution_id,
            location_id=location_id,
            appointment_id=appointment_id,
            contact_id=contact_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            action=action,
            status=GoTrackerAppointmentWritebackStatus.PENDING.value,
            previous_start_time=previous_start_time,
            requested_start_time=_parse_dt(requested_start_time),
            provider_id=provider_id,
            status_id=status_id,
            confirmed=confirmed,
            preconfirmed=preconfirmed,
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def acquire_appointment_lock(
        self,
        *,
        institution_id: str,
        appointment_id: str,
    ) -> None:
        """Serialize GoTracker writes for one tenant-scoped appointment.

        GoTracker's cloud row only has one pending write slot per appointment.
        Holding a transaction-level advisory lock while we check/send/record
        prevents two workers from sending overlapping writes for the same
        appointment.
        """
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:lock_key, 0)"
                ")"
            ),
            {"lock_key": f"gotracker-writeback:{institution_id}:{appointment_id}"},
        )

    async def pending_for_appointment(
        self,
        *,
        institution_id: str,
        appointment_id: str,
    ) -> GoTrackerAppointmentWriteback | None:
        return (
            await self.session.execute(
                select(GoTrackerAppointmentWriteback)
                .where(
                    GoTrackerAppointmentWriteback.institution_id == institution_id,
                    GoTrackerAppointmentWriteback.appointment_id == appointment_id,
                    GoTrackerAppointmentWriteback.status
                    == GoTrackerAppointmentWritebackStatus.PENDING.value,
                )
                .order_by(
                    GoTrackerAppointmentWriteback.created_at.asc(),
                    GoTrackerAppointmentWriteback.id.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    async def get_pending(
        self,
        *,
        writeback_id: str,
    ) -> GoTrackerAppointmentWriteback | None:
        return (
            await self.session.execute(
                select(GoTrackerAppointmentWriteback).where(
                    GoTrackerAppointmentWriteback.id == writeback_id,
                    GoTrackerAppointmentWriteback.status
                    == GoTrackerAppointmentWritebackStatus.PENDING.value,
                )
            )
        ).scalar_one_or_none()

    async def list_stale_pending(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[GoTrackerAppointmentWriteback]:
        return list(
            (
                await self.session.execute(
                    select(GoTrackerAppointmentWriteback)
                    .where(
                        GoTrackerAppointmentWriteback.status
                        == GoTrackerAppointmentWritebackStatus.PENDING.value,
                        GoTrackerAppointmentWriteback.created_at <= cutoff,
                    )
                    .order_by(
                        GoTrackerAppointmentWriteback.created_at.asc(),
                        GoTrackerAppointmentWriteback.id.asc(),
                    )
                    .limit(limit)
                )
            ).scalars()
        )

    async def complete_latest(
        self,
        *,
        institution_id: str,
        appointment_id: str,
        source_event_id: str | None = None,
    ) -> GoTrackerAppointmentWriteback | None:
        row = await self._latest_pending(
            institution_id=institution_id,
            appointment_id=appointment_id,
        )
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        row.status = GoTrackerAppointmentWritebackStatus.COMPLETED.value
        row.completed_event_id = source_event_id
        row.completed_at = now
        row.updated_at = now
        await self.session.flush()
        return row

    async def complete(
        self,
        row: GoTrackerAppointmentWriteback,
        *,
        source_event_id: str | None = None,
    ) -> GoTrackerAppointmentWriteback:
        now = datetime.now(timezone.utc)
        row.status = GoTrackerAppointmentWritebackStatus.COMPLETED.value
        row.completed_event_id = source_event_id
        row.completed_at = now
        row.updated_at = now
        await self.session.flush()
        return row

    async def fail_latest(
        self,
        *,
        institution_id: str,
        appointment_id: str,
        source_event_id: str | None = None,
        error: str | None = None,
    ) -> GoTrackerAppointmentWriteback | None:
        row = await self._latest_pending(
            institution_id=institution_id,
            appointment_id=appointment_id,
        )
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        row.status = GoTrackerAppointmentWritebackStatus.FAILED.value
        row.failed_event_id = source_event_id
        row.error_message = sanitize_provider_error(error) if error else None
        row.failed_at = now
        row.updated_at = now
        await self.session.flush()
        return row

    async def fail(
        self,
        row: GoTrackerAppointmentWriteback,
        *,
        source_event_id: str | None = None,
        error: str | None = None,
    ) -> GoTrackerAppointmentWriteback:
        now = datetime.now(timezone.utc)
        row.status = GoTrackerAppointmentWritebackStatus.FAILED.value
        row.failed_event_id = source_event_id
        row.error_message = sanitize_provider_error(error) if error else None
        row.failed_at = now
        row.updated_at = now
        await self.session.flush()
        return row

    async def _current_start_time(
        self,
        *,
        institution_id: str,
        appointment_id: str,
    ) -> datetime | None:
        row = (
            await self.session.execute(
                select(AppointmentWorkingSet).where(
                    AppointmentWorkingSet.institution_id == institution_id,
                    AppointmentWorkingSet.nexhealth_appointment_id == appointment_id,
                )
            )
        ).scalar_one_or_none()
        return row.start_time if row is not None else None

    async def _latest_pending(
        self,
        *,
        institution_id: str,
        appointment_id: str,
    ) -> GoTrackerAppointmentWriteback | None:
        return (
            await self.session.execute(
                select(GoTrackerAppointmentWriteback)
                .where(
                    GoTrackerAppointmentWriteback.institution_id == institution_id,
                    GoTrackerAppointmentWriteback.appointment_id == appointment_id,
                    GoTrackerAppointmentWriteback.status
                    == GoTrackerAppointmentWritebackStatus.PENDING.value,
                )
                .order_by(
                    GoTrackerAppointmentWriteback.created_at.desc(),
                    GoTrackerAppointmentWriteback.id.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()


def action_for_status_write(
    *,
    status_id: int | None,
    confirmed: bool | None,
    preconfirmed: bool | None,
) -> str:
    if status_id == 3:
        return GoTrackerAppointmentWritebackAction.CANCEL.value
    if confirmed is not None or preconfirmed is not None:
        return GoTrackerAppointmentWritebackAction.CONFIRM.value
    return GoTrackerAppointmentWritebackAction.STATUS.value
