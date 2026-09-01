"""Reading and working the leads that landed.

Until now an enquiry could arrive and then be invisible: nothing listed it,
nothing showed it, and nothing let a staff member write down what happened when
they rang. A store nobody can see is indistinguishable from a store that is not
working, which is most of why this exists.

**Lifecycle is derived, not stored twice.** Whether somebody is a lead or a
patient is decided by one fact — does the contact they are linked to carry a
practice-software id — so the stage shown here cannot drift from reality the way
a hand-maintained property does. ``status`` remains what the campaign sets as it
works the lead; the stage is what the clinic sees.

**PII is masked**, exactly as it is on the patients list: a lead's phone and
email belong to somebody who is not a patient and has consented to little, so
they are shown last-four and domain-only until someone asks for them. Notes are
the exception and are returned in full to the staff working the lead, because a
note nobody can read is not a note.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session_dep
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.campaign_enquiry import CampaignEnquiry, EnquiryStatus
from src.app.models.contact import Contact
from src.app.models.user import User
from src.app.services.audit_decorator import audit
from src.app.services.sms_privacy import hash_email, hash_phone, mask_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/enquiries", tags=["Enquiries"])

_InstitutionUser = Annotated[User, Depends(get_current_institution_admin)]
_Session = Annotated[AsyncSession, Depends(get_db_session_dep)]

#: What the clinic sees, derived rather than stored.
Stage = Literal["lead", "contacted", "registered", "booked"]


def _mask_email(email: str | None) -> str | None:
    """Enough to recognise a person, not enough to contact them."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    head = local[0] if local else ""
    return f"{head}{'*' * max(len(local) - 1, 1)}@{domain}"


def _stage(row: CampaignEnquiry) -> Stage:
    """One fact decides it: is there a record in the practice software.

    Ordered most-progressed first, so a booked lead does not read as merely
    registered.
    """
    if row.status == EnquiryStatus.BOOKED.value:
        return "booked"
    if row.contact_id:
        return "registered"
    if row.status in (EnquiryStatus.NEW.value,):
        return "lead"
    return "contacted"


class EnquiryListItem(BaseModel):
    id: str
    first_name: str | None
    last_name: str | None
    phone_masked: str | None
    email_masked: str | None
    status: str
    stage: Stage
    source: str
    #: Set once they exist in the practice software, so the page can link across.
    contact_id: str | None
    has_notes: bool
    created_at: datetime
    updated_at: datetime


class EnquiryListResponse(BaseModel):
    items: list[EnquiryListItem]
    total: int
    limit: int
    offset: int


class EnquiryDetail(EnquiryListItem):
    #: Returned in full: a note nobody can read is not a note.
    notes: str | None
    attribution: dict | None
    external_ref: str | None
    intake_key: str
    location_id: str | None


class EnquiryUpdate(BaseModel):
    notes: str | None = None
    status: str | None = Field(default=None)


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No institution")
    return str(user.institution_id)


def _item(row: CampaignEnquiry) -> EnquiryListItem:
    return EnquiryListItem(
        id=str(row.id),
        first_name=row.first_name,
        last_name=row.last_name,
        phone_masked=mask_phone(row.phone) if row.phone else None,
        email_masked=_mask_email(row.email),
        status=row.status,
        stage=_stage(row),
        source=row.source,
        contact_id=str(row.contact_id) if row.contact_id else None,
        has_notes=bool(row.notes_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=EnquiryListResponse)
@limiter.limit(RATE_READ)
async def list_enquiries(
    request: Request,
    current_user: _InstitutionUser,
    session: _Session,
    status_filter: str | None = Query(default=None, alias="status"),
    stage: Stage | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EnquiryListResponse:
    """The leads that have landed, newest first."""
    institution_id = _institution_id(current_user)
    conditions = [CampaignEnquiry.institution_id == institution_id]

    if status_filter:
        conditions.append(CampaignEnquiry.status == status_filter)

    if search:
        term = search.strip()
        # A phone or email is encrypted, so it cannot be matched with LIKE.
        # Hashing the search term turns an exact identifier into an exact
        # lookup, which is what someone pasting a number actually wants; names
        # stay a prefix search.
        phone_h = hash_phone(term)
        email_h = hash_email(term) if "@" in term else None
        clauses = [
            CampaignEnquiry.first_name.ilike(f"{term}%"),
            CampaignEnquiry.last_name.ilike(f"{term}%"),
        ]
        if phone_h:
            clauses.append(CampaignEnquiry.phone_hash == phone_h)
        if email_h:
            clauses.append(CampaignEnquiry.email_hash == email_h)
        conditions.append(or_(*clauses))

    total = (
        await session.execute(
            select(func.count()).select_from(CampaignEnquiry).where(*conditions)
        )
    ).scalar() or 0

    rows = (
        (
            await session.execute(
                select(CampaignEnquiry)
                .where(*conditions)
                .order_by(CampaignEnquiry.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = [_item(row) for row in rows]
    if stage:
        # Derived, so it is filtered after mapping rather than in SQL. The page
        # size bounds the cost, and keeping one definition of stage is worth
        # more than pushing it into the query.
        items = [item for item in items if item.stage == stage]
    return EnquiryListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{enquiry_id}", response_model=EnquiryDetail)
@limiter.limit(RATE_READ)
async def get_enquiry(
    request: Request,
    enquiry_id: str,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquiryDetail:
    row = await _load(session, enquiry_id, _institution_id(current_user))
    return EnquiryDetail(
        **_item(row).model_dump(),
        notes=row.notes,
        attribution=row.attribution,
        external_ref=row.external_ref,
        intake_key=row.intake_key,
        location_id=str(row.location_id) if row.location_id else None,
    )


@router.patch("/{enquiry_id}", response_model=EnquiryDetail)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"campaign_enquiry:{kw.get('enquiry_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def update_enquiry(
    request: Request,
    enquiry_id: str,
    data: EnquiryUpdate,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquiryDetail:
    """Write down what happened, or move the lead along."""
    row = await _load(session, enquiry_id, _institution_id(current_user))
    if data.status is not None:
        if data.status not in {s.value for s in EnquiryStatus}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown status")
        row.status = data.status
    if data.notes is not None:
        # Empty string clears; None means "not supplied", so a status-only
        # update cannot silently wipe somebody's notes.
        row.notes = data.notes or None
    await session.flush()
    return EnquiryDetail(
        **_item(row).model_dump(),
        notes=row.notes,
        attribution=row.attribution,
        external_ref=row.external_ref,
        intake_key=row.intake_key,
        location_id=str(row.location_id) if row.location_id else None,
    )


async def _load(session: AsyncSession, enquiry_id: str, institution_id: str):
    row = (
        await session.execute(
            select(CampaignEnquiry).where(
                CampaignEnquiry.id == enquiry_id,
                CampaignEnquiry.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row
