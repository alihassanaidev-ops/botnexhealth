"""Staff-initiated do-not-contact admin API (Plan 12 / Scope §11).

The compliance gate already *honors* a `DoNotContact` (blocks every channel for
its scope tier), and automated writers exist (SMS STOP → suppression; spoken
opt-out → location DNC). This route is the missing **privileged staff entry
point** to record an opt-out received off-channel — in person, by phone to a
human, or by email — which otherwise could not be honored without a DB edit.
With frequency caps dropped, honoring every opt-out is the primary legal backstop.

INSTITUTION_ADMIN-scoped: an admin manages DNCs within their own institution
(location or institution tier). DSO/group-wide ("remove me everywhere across the
group") is a separate GROUP_ADMIN concern — rejected here and tracked as a follow-up.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.sms_consent import DncScope, DoNotContact
from src.app.models.user import User
from src.app.services.audit import log_audit
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.sms_privacy import hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/do-not-contact", tags=["Do Not Contact"])


# ── Models ───────────────────────────────────────────────────────────────────


class DncCreateRequest(BaseModel):
    phone: str = Field(min_length=3, description="Patient phone (E.164 preferred)")
    scope: Literal["location", "institution"] = "institution"
    location_id: str | None = None
    contact_id: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class DncReleaseRequest(BaseModel):
    phone: str = Field(min_length=3)


class DncRecord(BaseModel):
    phone_masked: str
    scope: str
    source: str
    reason: str | None
    location_id: str | None
    contact_id: str | None
    created_at: datetime


class DncListResponse(BaseModel):
    records: list[DncRecord]


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", response_model=DncRecord, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
async def add_do_not_contact(
    request: Request,
    body: DncCreateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> DncRecord:
    """Record a staff-initiated do-not-contact (blocks all channels for the scope)."""
    institution_id = current_user.institution_id
    if not institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")
    if body.scope == "location" and not body.location_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "location scope requires location_id")

    async with get_db_session() as session:
        compliance = SmsComplianceService(session)
        row = await compliance.set_do_not_contact(
            institution_id=str(institution_id),
            phone=body.phone,
            scope=DncScope(body.scope),
            location_id=body.location_id,
            contact_id=body.contact_id,
            reason=body.reason,
            created_by_user_id=str(current_user.id),
        )
        await session.commit()
        record = _to_record(row)

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.DO_NOT_CONTACT_CREATE,
        target_resource=f"do_not_contact:{hash_for_logging(body.phone)}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"scope": body.scope, "phone_hash": hash_for_logging(body.phone)},
        institution_id=str(institution_id),
        user_id=str(current_user.id),
        location_id=body.location_id,
    )
    logger.info(
        "do_not_contact created: institution=%s scope=%s phone_hash=%s by=%s",
        institution_id, body.scope, hash_for_logging(body.phone), current_user.id,
    )
    return record


@router.delete("", status_code=status.HTTP_200_OK)
@limiter.limit(RATE_WRITE)
async def remove_do_not_contact(
    request: Request,
    body: DncReleaseRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> dict[str, bool]:
    """Release an active do-not-contact for a phone. Idempotent."""
    institution_id = current_user.institution_id
    if not institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    async with get_db_session() as session:
        released = await SmsComplianceService(session).release_do_not_contact(
            institution_id=str(institution_id),
            phone=body.phone,
            released_by_user_id=str(current_user.id),
        )
        await session.commit()

    if released is not None:
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.DO_NOT_CONTACT_RELEASE,
            target_resource=f"do_not_contact:{hash_for_logging(body.phone)}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"phone_hash": hash_for_logging(body.phone)},
            institution_id=str(institution_id),
            user_id=str(current_user.id),
        )
    return {"released": released is not None}


@router.get("", response_model=DncListResponse)
@limiter.limit(RATE_READ)
async def list_do_not_contact(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> DncListResponse:
    """List active do-not-contact records for the caller's institution (masked)."""
    institution_id = current_user.institution_id
    if not institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    async with get_db_session() as session:
        rows = (
            await session.execute(
                select(DoNotContact)
                .where(
                    DoNotContact.institution_id == str(institution_id),
                    DoNotContact.is_active.is_(True),
                )
                .order_by(DoNotContact.created_at.desc())
                .limit(500)
            )
        ).scalars().all()
    return DncListResponse(records=[_to_record(r) for r in rows])


def _to_record(row: DoNotContact) -> DncRecord:
    return DncRecord(
        phone_masked=row.phone_masked,
        scope=row.scope,
        source=row.source,
        reason=row.reason,
        location_id=row.location_id,
        contact_id=row.contact_id,
        created_at=row.created_at,
    )
