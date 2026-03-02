"""Universal appointment type endpoints."""

from fastapi import APIRouter, Depends, HTTPException

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


@router.post("", response_model=UniversalAppointmentType)
async def create_appointment_type(
    req: CreateApptTypeRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    if not isinstance(pms, SupportsAppointmentTypeCreation):
        raise HTTPException(400, "This PMS does not support creating appointment types")
    return await pms.create_appointment_type(req.name, req.duration_minutes, req.descriptor_ids)
