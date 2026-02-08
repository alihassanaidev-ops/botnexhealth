"""Universal slot endpoints."""

from fastapi import APIRouter, Depends, Query

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_tenant_pms
from src.app.pms.models import UniversalSlot

router = APIRouter(prefix="/slots", tags=["Slots"])


@router.get("", response_model=list[UniversalSlot])
async def get_available_slots(
    start_date: str,
    days: int = 7,
    provider_id: str | None = None,
    appointment_type_id: str | None = None,
    operatory_ids: list[str] | None = Query(None),
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.get_available_slots(
        start_date=start_date,
        days=days,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
        operatory_ids=operatory_ids,
    )
