"""Universal patient endpoints."""

from fastapi import APIRouter, Depends

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_tenant_pms
from src.app.pms.models import PatientCreateRequest, UniversalPatient

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.get("", response_model=list[UniversalPatient])
async def search_patients(
    q: str = "",
    email: str | None = None,
    phone_number: str | None = None,
    date_of_birth: str | None = None,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.search_patients(
        q, email=email, phone_number=phone_number, date_of_birth=date_of_birth
    )


@router.post("")
async def create_patient(
    req: PatientCreateRequest,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.create_patient(req)
