"""Apply a location's configured booking policy to PMS slots.

``slot_filter`` is deliberately pure — it takes rows, not a database. This
module is the thin layer that fetches those rows for one location, so every
caller clips identically instead of each one re-implementing the query and
quietly omitting a control.

It exists because they did. The dashboard slot route and the voice agent both
loaded operating hours and breaks and called :func:`filter_slots`; the patient
booking page and the campaign booking node called the PMS adapter and returned
whatever came back. Same clinic, same appointment type, different answers —
and the surface that skipped the checks was the one patients actually touch.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots, merge_buffer_minutes

logger = logging.getLogger(__name__)

__all__ = ["filter_slots_for_location", "provider_buffer_minutes"]


def _strip_source_prefix(value: str | None) -> str | None:
    if not value or "-" not in value:
        return value
    prefix, raw_id = value.split("-", 1)
    return raw_id if prefix in {"nh", "gt"} else value


def _source_id_for_pms(source: str | None, provider_id: str | None) -> str | None:
    """Match the ``InstitutionProvider.source_id`` convention (``nh-``/``gt-``)."""
    if not provider_id:
        return None
    if provider_id.startswith(("nh-", "gt-")):
        return provider_id
    if source == "nexhealth":
        return f"nh-{provider_id}"
    if source == "gotracker":
        return f"gt-{provider_id}"
    return provider_id


async def provider_buffer_minutes(
    session: AsyncSession,
    *,
    location_id: str,
    provider_id: str | None,
    pms_source: str | None,
    requested_buffer: int = 0,
) -> int:
    """The effective lead-time buffer for one provider at one location.

    Returns ``requested_buffer`` unchanged when the provider is unknown — a
    missing row must not silently drop a buffer the caller already asked for.
    """
    source_id = _source_id_for_pms(pms_source, provider_id)
    if not source_id:
        return max(0, requested_buffer)
    row = (
        await session.execute(
            select(InstitutionProvider.buffer_minutes).where(
                InstitutionProvider.source_id == source_id,
                InstitutionProvider.location_id == str(location_id),
            )
        )
    ).one_or_none()
    if row is None:
        return max(0, requested_buffer)
    return merge_buffer_minutes(requested_buffer, max(0, int(row.buffer_minutes or 0)))


async def filter_slots_for_location(
    session: AsyncSession,
    location: InstitutionLocation,
    slots: list[UniversalSlot],
    *,
    buffer_minutes: int = 0,
    now: datetime | None = None,
) -> list[UniversalSlot]:
    """Clip *slots* to what this location is actually open for.

    Operating hours, recurring breaks and the lead-time buffer, in one pass, in
    the location's own timezone. A location with no hours configured is not
    clipped — that is :func:`filter_slots`' documented behaviour and is left
    alone here so adding this call cannot hide slots from a clinic that never
    set hours up.
    """
    if not slots:
        return slots

    location_id = str(location.id)
    operating_hours = list(
        (
            await session.execute(
                select(LocationOperatingHours).where(
                    LocationOperatingHours.location_id == location_id
                )
            )
        )
        .scalars()
        .all()
    )
    breaks = list(
        (
            await session.execute(
                select(LocationBreak).where(LocationBreak.location_id == location_id)
            )
        )
        .scalars()
        .all()
    )
    return filter_slots(
        slots=slots,
        operating_hours=operating_hours,
        breaks=breaks,
        timezone=location.timezone or "UTC",
        buffer_minutes=buffer_minutes,
        now=now,
    )
