"""Universal appointment type endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.app.api.deps import get_current_institution_or_location_admin
from src.app.models.user import User
from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalAppointmentType
from pydantic import BaseModel

router = APIRouter(prefix="/appointment-types", tags=["Appointment Types"])


@router.get("", response_model=list[UniversalAppointmentType])
async def list_appointment_types(
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.list_appointment_types()


class CreateApptTypeRequest(BaseModel):
    name: str
    duration_minutes: int
    descriptor_ids: list[str] = []
    provider_ids: list[str] = []
    operatory_ids: list[str] = []
    bookable_online: bool | None = None


@router.post("", response_model=UniversalAppointmentType)
async def create_appointment_type(
    req: CreateApptTypeRequest,
    _: Annotated[User, Depends(get_current_institution_or_location_admin)],
    pms: PMSAdapter = Depends(get_institution_pms),
):
    if not isinstance(pms, SupportsAppointmentTypeCreation):
        raise HTTPException(400, "This PMS does not support creating appointment types")
    if pms.source == "gotracker" and not req.provider_ids:
        raise HTTPException(400, "GoTracker appointment types require at least one provider")
    return await pms.create_appointment_type(
        req.name,
        req.duration_minutes,
        req.descriptor_ids,
        provider_ids=req.provider_ids,
        operatory_ids=req.operatory_ids,
        bookable_online=req.bookable_online,
    )
