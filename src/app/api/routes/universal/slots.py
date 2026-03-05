"""Universal slot endpoints."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from src.app.database import get_db_session
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots

router = APIRouter(prefix="/slots", tags=["Slots"])


@router.get("", response_model=list[UniversalSlot])
async def get_available_slots(
    request: Request,
    start_date: str,
    days: int = 7,
    provider_id: str | None = None,
    appointment_type_id: str | None = None,
    operatory_ids: list[str] | None = Query(None),
    pms: PMSAdapter = Depends(get_institution_pms),
):
    # 1. Fetch raw slots from PMS
    slots = await pms.get_available_slots(
        start_date=start_date,
        days=days,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
        operatory_ids=operatory_ids,
    )

    # 2. Apply clinic hours filtering (if configured)
    location = getattr(request.state, "location", None)
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

        if operating_hours:
            slots = filter_slots(
                slots=slots,
                operating_hours=operating_hours,
                breaks=breaks,
                timezone=location.timezone or "UTC",
            )

    return slots
