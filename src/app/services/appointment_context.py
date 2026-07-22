"""Resolve authoritative appointment context for patient/staff notifications.

Approach B: notifications are built from our own structured data — the real PMS
booking (provider, time, service) captured when the ``book_appointment`` tool
ran during the call — rather than from Retell's free-text post-call message.

The booking result is read from the persisted ``retell_function_invocations``
row (keyed by the Retell call id) and its PMS ids are resolved to display names
via the institution's cached provider / appointment-type setup data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.retell_function_invocation import RetellFunctionInvocation

logger = logging.getLogger(__name__)


@dataclass
class BookingContext:
    """Structured, authoritative details of an appointment booked on a call."""

    booked: bool
    appointment_id: str | None = None
    patient_id: str | None = None
    provider_name: str | None = None
    appointment_datetime: str | None = None
    service: str | None = None


def _strip_pms_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).removeprefix("nh-")


async def resolve_booking_context(
    session: AsyncSession,
    *,
    institution_id: str,
    retell_call_id: str,
    timezone: str = "UTC",
) -> BookingContext | None:
    """Return the booked-appointment context for a call, or ``None`` if no
    appointment was successfully booked during it.

    Reads the successful ``book_appointment`` invocation persisted during the
    call and resolves the provider / appointment-type ids to names. ``timezone``
    is the location's IANA timezone; the appointment time is rendered in it.
    """
    row = (
        (
            await session.execute(
                select(RetellFunctionInvocation)
                .where(
                    RetellFunctionInvocation.call_id == retell_call_id,
                    RetellFunctionInvocation.function_name == "book_appointment",
                )
                .order_by(RetellFunctionInvocation.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if not row or not row.result_json:
        return None

    try:
        result = json.loads(row.result_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(result, dict) or not result.get("success"):
        return None

    provider_name = await _resolve_provider_name(
        session, institution_id, result.get("provider_id")
    )
    service = await _resolve_service_name(
        session, institution_id, result.get("appointment_type_id")
    )

    return BookingContext(
        booked=True,
        appointment_id=result.get("id"),
        patient_id=result.get("patient_id"),
        provider_name=provider_name,
        appointment_datetime=_format_datetime(result.get("start"), timezone),
        service=service,
    )


async def _resolve_provider_name(
    session: AsyncSession, institution_id: str, provider_id: str | None
) -> str | None:
    if not provider_id:
        return None
    row = (
        (
            await session.execute(
                select(InstitutionProvider).where(
                    InstitutionProvider.institution_id == institution_id,
                    InstitutionProvider.source_id.in_(
                        [str(provider_id), _strip_pms_prefix(provider_id)]
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    if not row:
        return None
    name = (row.name or "").strip()
    if not name:
        name = " ".join(p for p in (row.first_name, row.last_name) if p).strip()
    return name or None


async def _resolve_service_name(
    session: AsyncSession, institution_id: str, appt_type_id: str | None
) -> str | None:
    if not appt_type_id:
        return None
    row = (
        (
            await session.execute(
                select(InstitutionAppointmentType).where(
                    InstitutionAppointmentType.institution_id == institution_id,
                    InstitutionAppointmentType.source_id.in_(
                        [str(appt_type_id), _strip_pms_prefix(appt_type_id)]
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    return (row.name.strip() if row and row.name else None) or None


def _format_datetime(iso: str | None, timezone: str = "UTC") -> str | None:
    """Format an ISO-8601 appointment start for display in the clinic's local
    timezone.

    NexHealth returns appointment ``start_time`` in UTC (trailing ``Z``), so we
    must convert to the location's timezone: the patient needs the clinic-local
    wall-clock time, not UTC and not their own timezone. Falls back to the raw
    string if parsing fails (never blank the confirmation on a hiccup)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    try:
        dt = dt.astimezone(ZoneInfo(timezone or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        pass  # unknown tz name — keep whatever offset the timestamp carried
    return dt.strftime("%a, %b %-d at %-I:%M %p %Z")
