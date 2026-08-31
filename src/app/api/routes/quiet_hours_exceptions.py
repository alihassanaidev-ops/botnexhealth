"""Compliance settings: quiet-hours exceptions (Item 20).

The clinic-facing surface for the three exception kinds — a date, a patient, a
kind of message. Every state change is audited under
``CAMPAIGN_COMPLIANCE_UPDATE``, which Item 32 declared and reserved for exactly
this: when it landed there was no compliance-settings endpoint to decorate, and
this is that endpoint.

Rejections are returned as 422 with the service's own explanation rather than a
generic message. An operator who has just locked their clinic out of contacting
anyone needs to be told which exception did it and what to change, at the moment
they save it — not to discover a week later that nothing has sent.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import time as time_type
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_institution_user
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.quiet_hours_exception import QuietHoursException
from src.app.models.user import User
from src.app.services.audit_decorator import audit
from src.app.services.automation.quiet_hours_exception_service import (
    QuietHoursExceptionError,
    QuietHoursExceptionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/compliance/quiet-hours/exceptions", tags=["Compliance Settings"]
)

_InstitutionUser = Annotated[User, Depends(get_current_institution_user)]


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )
    return str(user.institution_id)


class QuietHoursExceptionRequest(BaseModel):
    location_id: str
    #: Omit for a rule covering every patient at the location.
    contact_id: str | None = None
    #: Omit for a rule that applies on every date.
    exception_date: date_type | None = None
    #: Omit to cover every kind of message. One of transactional_care / recall /
    #: sales / marketing when set.
    content_class: str | None = Field(default=None, max_length=40)
    #: True prevents contact entirely; the window fields are then ignored.
    is_blocked: bool = False
    open_time: time_type | None = None
    close_time: time_type | None = None
    reason: str | None = None


class QuietHoursExceptionUpdate(BaseModel):
    exception_date: date_type | None = None
    content_class: str | None = Field(default=None, max_length=40)
    is_blocked: bool | None = None
    open_time: time_type | None = None
    close_time: time_type | None = None
    reason: str | None = None


class QuietHoursExceptionResponse(BaseModel):
    id: str
    location_id: str
    contact_id: str | None
    exception_date: date_type | None
    content_class: str | None
    is_blocked: bool
    open_time: time_type | None
    close_time: time_type | None
    reason: str | None

    @classmethod
    def from_model(cls, row: QuietHoursException) -> "QuietHoursExceptionResponse":
        return cls(
            id=str(row.id),
            location_id=str(row.location_id),
            contact_id=str(row.contact_id) if row.contact_id else None,
            exception_date=row.exception_date,
            content_class=row.content_class,
            is_blocked=row.is_blocked,
            open_time=row.open_time,
            close_time=row.close_time,
            reason=row.reason,
        )


async def _owned_location_or_404(session, location_id: str, institution_id: str):
    location = await session.get(InstitutionLocation, location_id)
    if location is None or str(location.institution_id) != institution_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


async def _owned_exception_or_404(session, exception_id: str, institution_id: str):
    row = await session.get(QuietHoursException, exception_id)
    if row is None or str(row.institution_id) != institution_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found"
        )
    return row


@router.get("", response_model=list[QuietHoursExceptionResponse])
async def list_exceptions(
    location_id: str,
    current_user: _InstitutionUser,
) -> list[QuietHoursExceptionResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        await _owned_location_or_404(session, location_id, inst_id)
        rows = await QuietHoursExceptionService(session).list_for_location(
            inst_id, location_id
        )
        return [QuietHoursExceptionResponse.from_model(row) for row in rows]


@router.post(
    "", response_model=QuietHoursExceptionResponse, status_code=status.HTTP_201_CREATED
)
@audit(
    AuditAction.CAMPAIGN_COMPLIANCE_UPDATE,
    resource=lambda *args, **kwargs: (
        f"quiet_hours_exception:create:"
        f"{getattr(kwargs.get('data'), 'location_id', 'unknown')}"
    ),
    actor=AuditActor.ADMIN,
)
async def create_exception(
    data: QuietHoursExceptionRequest,
    current_user: _InstitutionUser,
) -> QuietHoursExceptionResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        await _owned_location_or_404(session, data.location_id, inst_id)
        try:
            row = await QuietHoursExceptionService(session).create(
                institution_id=inst_id,
                location_id=data.location_id,
                contact_id=data.contact_id,
                exception_date=data.exception_date,
                content_class=data.content_class,
                is_blocked=data.is_blocked,
                open_time=data.open_time,
                close_time=data.close_time,
                reason=data.reason,
            )
        except QuietHoursExceptionError as exc:
            # Rolled back by the session context manager: an exception that
            # would silence the clinic must not survive the request that
            # created it.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return QuietHoursExceptionResponse.from_model(row)


@router.patch("/{exception_id}", response_model=QuietHoursExceptionResponse)
@audit(
    AuditAction.CAMPAIGN_COMPLIANCE_UPDATE,
    resource=lambda *args, **kwargs: (
        f"quiet_hours_exception:update:{kwargs.get('exception_id', 'unknown')}"
    ),
    actor=AuditActor.ADMIN,
)
async def update_exception(
    exception_id: str,
    data: QuietHoursExceptionUpdate,
    current_user: _InstitutionUser,
) -> QuietHoursExceptionResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        row = await _owned_exception_or_404(session, exception_id, inst_id)
        fields = data.model_dump(exclude_unset=True)
        try:
            await QuietHoursExceptionService(session).update(row, **fields)
        except QuietHoursExceptionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return QuietHoursExceptionResponse.from_model(row)


@router.delete("/{exception_id}", status_code=status.HTTP_204_NO_CONTENT)
@audit(
    AuditAction.CAMPAIGN_COMPLIANCE_UPDATE,
    resource=lambda *args, **kwargs: (
        f"quiet_hours_exception:delete:{kwargs.get('exception_id', 'unknown')}"
    ),
    actor=AuditActor.ADMIN,
)
async def delete_exception(
    exception_id: str,
    current_user: _InstitutionUser,
) -> None:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        row = await _owned_exception_or_404(session, exception_id, inst_id)
        await QuietHoursExceptionService(session).delete(row)
