"""Read-only appointment sync/status projection for institution users."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, nullslast, or_, select

from src.app.api.deps import get_current_institution_or_location_user
from src.app.api.deps_scope import bind_active_location
from src.app.api.routes.calls import _location_scope_id
from src.app.database import get_db_session
from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.contact import Contact
from src.app.models.user import User

router = APIRouter(prefix="/institution/appointment-sync", tags=["Appointment Sync"])


class AppointmentSyncItem(BaseModel):
    id: str
    appointment_id: str
    patient_id: str | None = None
    contact_id: str | None = None
    patient_name: str | None = None
    location_id: str | None = None
    provider_id: str | None = None
    appointment_type_id: str | None = None
    start_time: str | None = None
    local_status: str
    gotracker_status_id: int | None = None
    gotracker_status_label: str | None = None
    is_confirmed: bool | None = None
    is_preconfirmed: bool | None = None
    last_status_source: str | None = None
    last_status_synced_at: str | None = None
    last_writeback_at: str | None = None
    last_event: str | None = None
    last_synced_at: str
    updated_at: str


class AppointmentSyncListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AppointmentSyncItem]


@router.get("", response_model=AppointmentSyncListResponse)
async def list_appointment_sync_status(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: str | None = Query(default=None, max_length=120),
    location_id: str | None = Query(default=None),
    gotracker_status_id: int | None = Query(default=None, ge=1, le=9),
) -> AppointmentSyncListResponse:
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Institution assignment required",
        )

    institution_id = str(current_user.institution_id)
    scoped_location_id = _location_scope_id(current_user)
    if scoped_location_id:
        # Location-scoped user: the requested location must be one of their
        # assigned set (defaults to primary), and the choice is bound into
        # the RLS context before the session below opens.
        effective_location_id = str(location_id) if location_id else scoped_location_id
        if effective_location_id not in current_user.allowed_location_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this location",
            )
        bind_active_location(current_user, effective_location_id)
    else:
        effective_location_id = location_id

    conditions = [AppointmentWorkingSet.institution_id == institution_id]
    if effective_location_id:
        conditions.append(AppointmentWorkingSet.location_id == effective_location_id)
    if gotracker_status_id is not None:
        conditions.append(AppointmentWorkingSet.gotracker_status_id == gotracker_status_id)
    if search:
        term = f"%{search.strip()}%"
        conditions.append(
            or_(
                AppointmentWorkingSet.nexhealth_appointment_id.ilike(term),
                AppointmentWorkingSet.nexhealth_patient_id.ilike(term),
                Contact.full_name.ilike(term),
            )
        )

    async with get_db_session() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(AppointmentWorkingSet)
                .outerjoin(Contact, AppointmentWorkingSet.contact_id == Contact.id)
                .where(*conditions)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(AppointmentWorkingSet, Contact.full_name)
                .outerjoin(Contact, AppointmentWorkingSet.contact_id == Contact.id)
                .where(*conditions)
                .order_by(
                    nullslast(desc(AppointmentWorkingSet.start_time)),
                    desc(AppointmentWorkingSet.updated_at),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()

    return AppointmentSyncListResponse(
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=[
            _appointment_sync_item(row, patient_name)
            for row, patient_name in rows
        ],
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _appointment_sync_item(
    row: AppointmentWorkingSet,
    patient_name: str | None,
) -> AppointmentSyncItem:
    return AppointmentSyncItem(
        id=str(row.id),
        appointment_id=row.nexhealth_appointment_id,
        patient_id=row.nexhealth_patient_id,
        contact_id=str(row.contact_id) if row.contact_id else None,
        patient_name=patient_name,
        location_id=str(row.location_id) if row.location_id else None,
        provider_id=row.provider_id,
        appointment_type_id=row.appointment_type_id,
        start_time=_iso(row.start_time),
        local_status=row.status,
        gotracker_status_id=row.gotracker_status_id,
        gotracker_status_label=row.gotracker_status_label,
        is_confirmed=row.is_confirmed,
        is_preconfirmed=row.is_preconfirmed,
        last_status_source=row.last_status_source,
        last_status_synced_at=_iso(row.last_status_synced_at),
        last_writeback_at=_iso(row.last_writeback_at),
        last_event=row.last_event,
        last_synced_at=_iso(row.last_synced_at) or "",
        updated_at=_iso(row.updated_at) or "",
    )
