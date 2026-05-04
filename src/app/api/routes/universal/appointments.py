"""Universal appointment endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.app.api.deps import get_current_institution_or_location_user
from src.app.api.rate_limit import RATE_WRITE, limiter
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import BookingRequest, BookingResult
from src.app.retell.security import hash_for_logging
from src.app.services.audit_decorator import audit

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.post("", response_model=BookingResult)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.BOOK_APPOINTMENT,
    resource=lambda *a, **kw: "appointment:book",
    actor=AuditActor.ADMIN,
)
async def book_appointment(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    req: BookingRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.book_appointment(req)


@router.patch("/{appointment_id}/cancel", response_model=BookingResult)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CANCEL_APPOINTMENT,
    resource=lambda *a, **kw: f"appointment:{hash_for_logging(str(kw.get('appointment_id'))) if kw.get('appointment_id') else 'unknown'}",
    actor=AuditActor.ADMIN,
)
async def cancel_appointment(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    appointment_id: str,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.cancel_appointment(appointment_id)


@router.post("/{old_appointment_id}/reschedule", response_model=BookingResult)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.RESCHEDULE_APPOINTMENT,
    resource=lambda *a, **kw: f"reschedule:old={hash_for_logging(str(kw.get('old_appointment_id'))) if kw.get('old_appointment_id') else 'unknown'}",
    actor=AuditActor.ADMIN,
)
async def reschedule_appointment(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    old_appointment_id: str,
    req: BookingRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.reschedule_appointment(old_appointment_id, req)
