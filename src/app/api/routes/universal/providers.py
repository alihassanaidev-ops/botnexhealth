"""Universal provider endpoints."""

from fastapi import APIRouter, Depends

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalProvider

router = APIRouter(prefix="/providers", tags=["Providers"])


@router.get("", response_model=list[UniversalProvider])
async def list_providers(
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.list_providers()
