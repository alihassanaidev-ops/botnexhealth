"""Universal slot endpoints."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from src.app.database import get_db_session
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import (
    apply_time_restriction,
    filter_slots,
    get_local_date_string,
    merge_buffer_minutes,
)

router = APIRouter(prefix="/slots", tags=["Slots"])


@router.get("", response_model=list[UniversalSlot])
async def get_available_slots(
    request: Request,
    start_date: str,
    days: int = 7,
    provider_id: str | None = None,
    appointment_type_id: str | None = None,
    operatory_ids: list[str] | None = Query(None),
    tz_offset: str | None = Query(None, description="Clinic UTC offset, e.g. -04:00"),
    buffer_minutes: int = Query(0, ge=0, le=1440, description="Minimum lead-time in minutes from now"),
    pms: PMSAdapter = Depends(get_institution_pms),
):
    # 1. Fetch raw slots from PMS
    slots = await pms.get_available_slots(
        start_date=start_date,
        days=days,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
        operatory_ids=operatory_ids,
        tz_offset=tz_offset,
    )

    # 2. Auto-fetch provider settings if provider_id given
    location = getattr(request.state, "location", None)
    provider_cutoff = None
    provider_source_id = _source_id_for_pms(pms.source, provider_id)
    normalized_provider_id = _strip_source_prefix(provider_id)
    if provider_id and location:
        async with get_db_session() as session:
            prov = (await session.execute(
                select(
                    InstitutionProvider.buffer_minutes,
                    InstitutionProvider.same_day_cutoff_time,
                ).where(
                    InstitutionProvider.source_id == provider_source_id,
                    InstitutionProvider.location_id == str(location.id),
                )
            )).one_or_none()
            if prov:
                provider_buffer = max(0, int(prov.buffer_minutes or 0))
                buffer_minutes = merge_buffer_minutes(buffer_minutes, provider_buffer)
                provider_cutoff = prov.same_day_cutoff_time

    # 3. Apply clinic hours + buffer filtering (if configured)
    if location:
        async with get_db_session() as session:
            hours_result = await session.execute(
                select(LocationOperatingHours).where(
                    LocationOperatingHours.location_id == location.id
                )
            )
            operating_hours = hours_result.scalars().all()

            breaks_result = await session.execute(
                select(LocationBreak).where(
                    LocationBreak.location_id == location.id
                )
            )
            breaks = breaks_result.scalars().all()

        slots = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=breaks,
            timezone=location.timezone or "UTC",
            buffer_minutes=buffer_minutes,
        )
    elif buffer_minutes > 0:
        from src.app.services.slot_filter import apply_buffer
        slots = apply_buffer(slots, buffer_minutes)

    # 4. Apply same-day cutoff time restriction
    if provider_cutoff and normalized_provider_id and location:
        today_str = get_local_date_string(location.timezone or "UTC")
        has_appts = await pms.has_provider_appointments_on_date(normalized_provider_id, today_str)
        slots = apply_time_restriction(
            slots=slots,
            cutoff_time=provider_cutoff,
            has_appointments_today=has_appts,
            timezone=location.timezone or "UTC",
        )

    return slots


def _strip_source_prefix(value: str | None) -> str | None:
    if not value or "-" not in value:
        return value
    prefix, raw_id = value.split("-", 1)
    return raw_id if prefix in {"nh", "gt"} else value


def _source_id_for_pms(source: str, provider_id: str | None) -> str | None:
    if not provider_id:
        return None
    if provider_id.startswith(("nh-", "gt-")):
        return provider_id
    if source == "nexhealth":
        return f"nh-{provider_id}"
    if source == "gotracker":
        return f"gt-{provider_id}"
    return provider_id
