"""Universal operatory endpoints."""

from fastapi import APIRouter, Depends

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalOperatory

router = APIRouter(prefix="/operatories", tags=["Operatories"])


@router.get("", response_model=list[UniversalOperatory])
async def list_operatories(
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.list_operatories()
