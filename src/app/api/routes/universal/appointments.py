"""Universal appointment endpoints."""

from fastapi import APIRouter, Depends

from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import BookingRequest, BookingResult

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.post("", response_model=BookingResult)
async def book_appointment(
    req: BookingRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.book_appointment(req)


@router.patch("/{appointment_id}/cancel", response_model=BookingResult)
async def cancel_appointment(
    appointment_id: str,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.cancel_appointment(appointment_id)


@router.post("/{old_appointment_id}/reschedule", response_model=BookingResult)
async def reschedule_appointment(
    old_appointment_id: str,
    req: BookingRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.reschedule_appointment(old_appointment_id, req)
