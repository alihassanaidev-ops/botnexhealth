"""SMS history and suppression APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.app.api.deps import get_current_admin, get_current_institution_or_location_user
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_consent import ConsentSource, ConsentStatus, SmsSuppression
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit, phi_reveal_audit
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.sms_privacy import hash_for_logging

admin_router = APIRouter(prefix="/admin/sms", tags=["Admin - SMS"])
institution_router = APIRouter(prefix="/institution/sms", tags=["SMS"])


class SmsLocationResponse(BaseModel):
    id: str
    institution_id: str
    institution_name: str
    location_name: str
    twilio_from_number: str | None


class SmsLogResponse(BaseModel):
    id: str
    timestamp: datetime
    from_number: str
    to_number_masked: str | None
    status: str
    provider_status: str | None
    message_sid: str | None
    error_message: str | None
    institution_location_id: str
    patient_contact_id: str | None
    call_id: str | None
    body_available: bool = True


class SmsLogListResponse(BaseModel):
    items: list[SmsLogResponse]
    total: int
    page: int
    size: int
    pages: int


class SuppressionCreateRequest(BaseModel):
    location_id: str
    phone: str = Field(..., min_length=3, max_length=50)
    reason: str | None = Field(None, max_length=500)


class SuppressionResponse(BaseModel):
    id: str
    institution_id: str
    location_id: str | None
    phone_masked: str
    is_active: bool
    source: str
    keyword: str | None
    reason: str | None
    created_at: datetime
    released_at: datetime | None


class RevealResponse(BaseModel):
    id: str
    value: str


@admin_router.get("/locations", response_model=list[SmsLocationResponse])
async def list_sms_locations(
    _: Annotated[User, Depends(get_current_admin)],
) -> list[SmsLocationResponse]:
    from src.app.models.institution import Institution

    async with get_db_session() as session:
        rows = (
            await session.execute(
                select(InstitutionLocation, Institution.name)
                .join(Institution, Institution.id == InstitutionLocation.institution_id)
                .where(InstitutionLocation.is_active.is_(True))
                .order_by(Institution.name.asc(), InstitutionLocation.name.asc())
            )
        ).all()
        return [
            SmsLocationResponse(
                id=str(location.id),
                institution_id=str(location.institution_id),
                institution_name=institution_name,
                location_name=location.name,
                twilio_from_number=location.twilio_from_number,
            )
            for location, institution_name in rows
        ]


@admin_router.get("/logs", response_model=SmsLogListResponse)
async def list_admin_sms_logs(
    _: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    institution_id: str | None = None,
    location_id: str | None = None,
) -> SmsLogListResponse:
    return await _list_sms_logs(
        page=page,
        size=size,
        institution_id=institution_id,
        location_id=location_id,
    )


@admin_router.get("/suppressions", response_model=list[SuppressionResponse])
async def list_suppressions(
    _: Annotated[User, Depends(get_current_admin)],
    institution_id: str | None = None,
    active_only: bool = True,
) -> list[SuppressionResponse]:
    async with get_db_session() as session:
        stmt = select(SmsSuppression).order_by(SmsSuppression.created_at.desc()).limit(200)
        if institution_id:
            stmt = stmt.where(SmsSuppression.institution_id == institution_id)
        if active_only:
            stmt = stmt.where(SmsSuppression.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        return [_suppression_response(row) for row in rows]


@admin_router.post("/suppressions", response_model=SuppressionResponse, status_code=status.HTTP_201_CREATED)
async def create_suppression(
    body: SuppressionCreateRequest,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> SuppressionResponse:
    async with get_db_session() as session:
        location = (
            await session.execute(
                select(InstitutionLocation).where(InstitutionLocation.id == body.location_id)
            )
        ).scalar_one_or_none()
        if not location:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        svc = SmsComplianceService(session)
        row = await svc.suppress(
            institution_id=location.institution_id,
            location_id=str(location.id),
            phone=body.phone,
            source=ConsentSource.MANUAL,
            reason=body.reason or "Manual suppression",
            created_by_user_id=str(current_admin.id),
        )
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.SMS_SUPPRESSION_CREATE,
            target_resource=f"sms_suppression:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"phone_hash": hash_for_logging(body.phone), "location_id": str(location.id)},
            institution_id=location.institution_id,
            user_id=str(current_admin.id),
            location_id=str(location.id),
        )
        await session.commit()
        return _suppression_response(row)


@admin_router.post("/suppressions/{suppression_id}/release", response_model=SuppressionResponse)
async def release_suppression(
    suppression_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> SuppressionResponse:
    async with get_db_session() as session:
        row = (
            await session.execute(
                select(SmsSuppression).where(SmsSuppression.id == suppression_id)
            )
        ).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suppression not found")
        if row.is_active:
            row.is_active = False
            row.released_by_user_id = str(current_admin.id)
            row.released_at = datetime.now(timezone.utc)
            svc = SmsComplianceService(session)
            await svc.record_consent_identity(
                institution_id=str(row.institution_id),
                location_id=str(row.location_id) if row.location_id else None,
                contact_id=str(row.contact_id) if row.contact_id else None,
                phone_hash=row.phone_hash,
                phone_masked=row.phone_masked,
                status=ConsentStatus.GRANTED,
                source=ConsentSource.MANUAL,
                reason="Manual suppression release",
                created_by_user_id=str(current_admin.id),
            )
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.SMS_SUPPRESSION_RELEASE,
            target_resource=f"sms_suppression:{row.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"phone_masked": row.phone_masked, "location_id": str(row.location_id) if row.location_id else None},
            institution_id=str(row.institution_id),
            user_id=str(current_admin.id),
            location_id=str(row.location_id) if row.location_id else None,
        )
        await session.commit()
        return _suppression_response(row)


@institution_router.get("/logs", response_model=SmsLogListResponse)
async def list_institution_sms_logs(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> SmsLogListResponse:
    institution_id, location_id = _scope(current_user)
    return await _list_sms_logs(page=page, size=size, institution_id=institution_id, location_id=location_id)


@institution_router.get("/logs/{sms_id}", response_model=SmsLogResponse)
async def get_institution_sms_log(
    sms_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
) -> SmsLogResponse:
    row = await _get_scoped_sms_log(sms_id, current_user)
    return _sms_log_response(row)


@institution_router.post("/logs/{sms_id}/reveal-phone", response_model=RevealResponse)
async def reveal_sms_phone(
    sms_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
) -> RevealResponse:
    row = await _get_scoped_sms_log(sms_id, current_user)
    async with _sms_reveal_audit(current_user, row, AuditAction.VIEW_FULL_PHONE):
        return RevealResponse(id=str(row.id), value=row.to_number or "")


@institution_router.post("/logs/{sms_id}/reveal-body", response_model=RevealResponse)
async def reveal_sms_body(
    sms_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
) -> RevealResponse:
    row = await _get_scoped_sms_log(sms_id, current_user)
    async with _sms_reveal_audit(current_user, row, AuditAction.VIEW_SMS_BODY):
        return RevealResponse(id=str(row.id), value=row.body or "")


async def _list_sms_logs(
    *,
    page: int,
    size: int,
    institution_id: str | None = None,
    location_id: str | None = None,
) -> SmsLogListResponse:
    async with get_db_session() as session:
        filters = []
        stmt = select(SmsHistoryLog).join(
            InstitutionLocation,
            SmsHistoryLog.institution_location_id == InstitutionLocation.id,
        )
        count_stmt = select(func.count()).select_from(SmsHistoryLog).join(
            InstitutionLocation,
            SmsHistoryLog.institution_location_id == InstitutionLocation.id,
        )
        if institution_id:
            filters.append(InstitutionLocation.institution_id == institution_id)
        if location_id:
            filters.append(InstitutionLocation.id == location_id)
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = int((await session.execute(count_stmt)).scalar() or 0)
        rows = (
            await session.execute(
                stmt.order_by(SmsHistoryLog.timestamp.desc()).offset((page - 1) * size).limit(size)
            )
        ).scalars().all()
        return SmsLogListResponse(
            items=[_sms_log_response(row) for row in rows],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if total else 0,
        )


async def _get_scoped_sms_log(sms_id: str, current_user: User) -> SmsHistoryLog:
    institution_id, location_id = _scope(current_user)
    async with get_db_session() as session:
        stmt = (
            select(SmsHistoryLog)
            .join(InstitutionLocation, SmsHistoryLog.institution_location_id == InstitutionLocation.id)
            .where(SmsHistoryLog.id == sms_id, InstitutionLocation.institution_id == institution_id)
        )
        if location_id:
            stmt = stmt.where(InstitutionLocation.id == location_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SMS log not found")
        return row


def _scope(user: User) -> tuple[str, str | None]:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Institution-scoped user required")
    if user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        if not user.location_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Location-scoped user required")
        return str(user.institution_id), str(user.location_id)
    return str(user.institution_id), None


def _sms_reveal_audit(user: User, row: SmsHistoryLog, action: AuditAction):
    """Two-row pre-then-post audit context for SMS-reveal endpoints.

    Use as ``async with _sms_reveal_audit(...): return Response(...)``.
    Writes INITIATED before the body decrypts PHI; refuses to proceed if
    the audit DB is down. Writes SUCCESS / FAILURE after.
    """
    return phi_reveal_audit(
        actor=AuditActor.ADMIN,
        action=action,
        target_resource=f"sms:{row.id}",
        institution_id=str(user.institution_id) if user.institution_id else None,
        user_id=str(user.id),
        location_id=str(row.institution_location_id),
        metadata={
            "location_id": str(row.institution_location_id),
            "phone_masked": row.to_number_masked,
        },
    )


def _sms_log_response(row: SmsHistoryLog) -> SmsLogResponse:
    return SmsLogResponse(
        id=str(row.id),
        timestamp=row.timestamp,
        from_number=row.from_number,
        to_number_masked=row.to_number_masked,
        status=row.status,
        provider_status=row.provider_status,
        message_sid=row.message_sid,
        error_message=row.error_message,
        institution_location_id=str(row.institution_location_id),
        patient_contact_id=str(row.patient_contact_id) if row.patient_contact_id else None,
        call_id=str(row.call_id) if row.call_id else None,
    )


def _suppression_response(row: SmsSuppression) -> SuppressionResponse:
    return SuppressionResponse(
        id=str(row.id),
        institution_id=str(row.institution_id),
        location_id=str(row.location_id) if row.location_id else None,
        phone_masked=row.phone_masked,
        is_active=row.is_active,
        source=row.source,
        keyword=row.keyword,
        reason=row.reason,
        created_at=row.created_at,
        released_at=row.released_at,
    )
