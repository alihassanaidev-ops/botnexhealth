"""Universal setup/capabilities endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import SetupStep

router = APIRouter(prefix="/setup", tags=["Setup"])


@router.get("/capabilities")
async def get_capabilities(pms: PMSAdapter = Depends(get_institution_pms)):
    return {
        "source": pms.source,
        "can_create_appointment_types": isinstance(pms, SupportsAppointmentTypeCreation),
        "can_link_availability": isinstance(pms, SupportsAvailabilityLinking),
        "setup_steps": await pms.get_setup_steps(),
    }


@router.get("/steps", response_model=list[SetupStep])
async def get_setup_steps(pms: PMSAdapter = Depends(get_institution_pms)):
    return await pms.get_setup_steps()


# ── NexHealth-specific admin setup ──────────────────────────────────────

@router.get("/descriptors")
async def list_descriptors(pms: PMSAdapter = Depends(get_institution_pms)):
    if not isinstance(pms, SupportsAppointmentTypeCreation):
        raise HTTPException(400, "This PMS does not use descriptors")
    return await pms.list_pms_descriptors()


class LinkAvailabilityRequest(BaseModel):
    provider_id: str
    appointment_type_ids: list[str]
    operatory_id: str
    days: list[str]
    start_time: str
    end_time: str


@router.post("/availabilities")
async def link_availability(
    req: LinkAvailabilityRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    if not isinstance(pms, SupportsAvailabilityLinking):
        raise HTTPException(400, "This PMS does not require availability linking")
    return await pms.link_availability(
        req.provider_id, req.appointment_type_ids, req.operatory_id,
        req.days, req.start_time, req.end_time,
    )


@router.get("/availabilities")
async def list_availabilities(pms: PMSAdapter = Depends(get_institution_pms)):
    if not isinstance(pms, SupportsAvailabilityLinking):
        raise HTTPException(400, "This PMS does not use availability linking")
    return await pms.list_availabilities()


class UpdateAvailabilityRequest(BaseModel):
    appointment_type_ids: list[str] | None = None
    days: list[str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    operatory_id: str | None = None
    active: bool | None = None


@router.patch("/availabilities/{availability_id}")
async def update_availability(
    availability_id: str,
    req: UpdateAvailabilityRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    if not isinstance(pms, SupportsAvailabilityLinking):
        raise HTTPException(400, "This PMS does not support availability updates")
    return await pms.update_availability(
        availability_id=availability_id,
        appointment_type_ids=req.appointment_type_ids,
        days=req.days,
        start_time=req.start_time,
        end_time=req.end_time,
        operatory_id=req.operatory_id,
        active=req.active,
    )
