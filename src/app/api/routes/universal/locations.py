"""Universal location endpoints."""

from fastapi import APIRouter, Depends

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_tenant_pms
from src.app.pms.models import UniversalLocation

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.get("", response_model=list[UniversalLocation])
async def list_locations(
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.list_locations()


@router.get("/{location_id}", response_model=UniversalLocation | None)
async def get_location(
    location_id: str,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.get_location(location_id)
