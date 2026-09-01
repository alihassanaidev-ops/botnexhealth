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
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session_dep
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.contact import Contact, LeadStatus
from src.app.models.user import User
from src.app.services.audit_decorator import audit
from src.app.services.automation.enquiry_intake_service import intake_enquiry
from src.app.services.automation.enquiry_trigger_service import (
    EnquiryTriggerService,
    enqueue_enquiry_workflow_dispatches,
)
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


def _stage(row: Contact) -> Stage:
    """One sentence: a contact with a practice-software id is a patient.

    Ordered most-progressed first, so somebody who booked does not read as
    merely registered. Derived every time rather than stored, so it cannot
    contradict the record it describes.
    """
    if row.lead_status == LeadStatus.BOOKED.value:
        return "booked"
    if row.nexhealth_patient_id:
        return "registered"
    if row.lead_status in (None, LeadStatus.NEW.value):
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


class EnquiryCreate(BaseModel):
    """A lead entered by hand.

    Staff take enquiries that never touch a form — somebody rings, or walks in,
    or a referral arrives by email. Decision C left this open; without it those
    people either go in a notebook or go nowhere.
    """

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    notes: str | None = None
    location_id: str | None = None
    #: What the person actually agreed to, asked rather than assumed. A staff
    #: member who did not ask should not be able to imply it by saving a form.
    consent_sms: bool = False
    consent_email: bool = False
    consent_wording: str | None = None


class EnquiryCreated(BaseModel):
    enquiry: "EnquiryDetail"
    #: False when this person was already on the list. Surfaced rather than
    #: silently deduplicated, so whoever typed it knows why no new row appeared.
    created: bool
    #: True when they turn out to already exist in the practice software.
    matched_existing_contact: bool


class EnquiryEnrol(BaseModel):
    workflow_id: str


class EnquiryEnrolled(BaseModel):
    enquiry: "EnquiryDetail"
    run_id: str
    contact_id: str
    #: False when this enquiry was already in this campaign.
    created: bool


class EnquiryUpdate(BaseModel):
    notes: str | None = None
    status: str | None = Field(default=None)


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No institution")
    return str(user.institution_id)


def _item(row: Contact) -> EnquiryListItem:
    return EnquiryListItem(
        id=str(row.id),
        first_name=row.first_name,
        last_name=row.last_name,
        phone_masked=mask_phone(row.phone) if row.phone else None,
        email_masked=_mask_email(row.email),
        status=row.lead_status or LeadStatus.NEW.value,
        stage=_stage(row),
        source=row.lead_source or "unknown",
        contact_id=str(row.id) if row.nexhealth_patient_id else None,
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
    conditions = [
        Contact.institution_id == institution_id,
        # Primary records only; an alias rolls up into what it was merged into.
        Contact.merged_into_id.is_(None),
        # This screen is the lead workspace, not the whole contact list. A
        # contact created by an inbound call has no lead_status and belongs on
        # Patients; one that came from an enquiry belongs here, and stays here
        # after registering so whoever was working them can see it landed.
        Contact.lead_status.isnot(None),
    ]

    if status_filter:
        conditions.append(Contact.lead_status == status_filter)

    if search:
        term = search.strip()
        # A phone or email is encrypted, so it cannot be matched with LIKE.
        # Hashing the search term turns an exact identifier into an exact
        # lookup, which is what someone pasting a number actually wants; names
        # stay a prefix search.
        phone_h = hash_phone(term)
        email_h = hash_email(term) if "@" in term else None
        clauses = [
            Contact.first_name.ilike(f"{term}%"),
            Contact.last_name.ilike(f"{term}%"),
        ]
        if phone_h:
            clauses.append(Contact.phone_hash == phone_h)
        if email_h:
            clauses.append(Contact.email_hash == email_h)
        conditions.append(or_(*clauses))

    total = (
        await session.execute(
            select(func.count()).select_from(Contact).where(*conditions)
        )
    ).scalar() or 0

    rows = (
        (
            await session.execute(
                select(Contact)
                .where(*conditions)
                .order_by(Contact.created_at.desc())
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


@router.post("", response_model=EnquiryCreated, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_CREATE,
    resource=lambda *a, **kw: "campaign_enquiry:manual",
    actor=AuditActor.ADMIN,
)
async def create_enquiry(
    request: Request,
    data: EnquiryCreate,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquiryCreated:
    """Enter a lead by hand.

    Goes through the same intake path a form does, rather than inserting a row
    directly, so deduplication against existing leads and patients and the
    recording of consent behave identically whichever way somebody arrived.
    Two paths that write the same table by different rules is how one of them
    ends up wrong.
    """
    institution_id = _institution_id(current_user)
    phone = (data.phone or "").strip() or None
    email = (data.email or "").strip() or None
    if not phone and not email:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A phone number or an email address is needed to reach them.",
        )

    consent_channels = tuple(
        channel
        for channel, agreed in (
            ("sms", data.consent_sms),
            ("email", data.consent_email),
        )
        if agreed
    )

    result = await intake_enquiry(
        session,
        institution_id=institution_id,
        # Ours, and unique per entry: a staff member has no submission id to
        # give, and reusing anything derived from the details would make a
        # second genuine enquiry from the same person look like a duplicate.
        intake_key=f"manual:{uuid4()}",
        source="manual",
        location_id=data.location_id,
        first_name=(data.first_name or "").strip() or None,
        last_name=(data.last_name or "").strip() or None,
        email=email,
        phone=phone,
        notes=data.notes,
        consent_channels=consent_channels,
        consent_wording=data.consent_wording,
    )
    dispatches = await EnquiryTriggerService(session).prepare_dispatches(
        institution_id=institution_id,
        location_id=data.location_id,
        contact=result.enquiry,
        intake_key=result.enquiry.intake_key,
        source="manual",
        created=result.created,
        matched_existing_contact=result.matched_existing_contact,
    )
    await session.flush()
    response = EnquiryCreated(
        enquiry=_detail(result.enquiry),
        created=result.created,
        matched_existing_contact=result.matched_existing_contact,
    )
    await session.commit()
    try:
        enqueued = enqueue_enquiry_workflow_dispatches(dispatches)
    except Exception:
        logger.exception(
            "manual enquiry workflow enqueue failed institution=%s contact=%s",
            institution_id,
            result.enquiry.id,
        )
    else:
        if enqueued:
            logger.info(
                "manual enquiry enqueued %d workflow(s) institution=%s contact=%s",
                enqueued,
                institution_id,
                result.enquiry.id,
            )
    return response


@router.post("/{enquiry_id}/enrol", response_model=EnquiryEnrolled)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_ENROLL,
    resource=lambda *a, **kw: f"campaign_enquiry:{kw.get('enquiry_id', '?')}:enrol",
    actor=AuditActor.ADMIN,
)
async def enrol_enquiry(
    request: Request,
    enquiry_id: str,
    data: EnquiryEnrol,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquiryEnrolled:
    """Put a lead into a campaign.

    Nothing is created here any more. A lead *is* a contact, so the run binds
    the record that already exists — which is the point of collapsing the two
    tables: there is no conversion step to get wrong, and no moment where the
    same person exists twice under different ids.
    """
    from src.app.models.automation_workflow import AutomationWorkflow
    from src.app.services.automation.enrollment_service import (
        AutomationWorkflowEnrollmentService,
    )

    institution_id = _institution_id(current_user)
    row = await _load(session, enquiry_id, institution_id)

    workflow = (
        await session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.id == data.workflow_id,
                AutomationWorkflow.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    if workflow.status != "active" or not workflow.current_version_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That campaign isn't published, so nothing would run.",
        )

    contact_id = str(row.id)
    location_id = str(workflow.location_id) if workflow.location_id else None
    run, created = await AutomationWorkflowEnrollmentService(session).enroll(
        institution_id=institution_id,
        workflow_id=str(workflow.id),
        workflow_version_id=str(workflow.current_version_id),
        contact_id=contact_id,
        location_id=location_id,
        trigger_type=workflow.trigger_type,
        trigger_ref_type="campaign_enquiry",
        trigger_ref_id=str(row.id),
        # Enrolling the same lead in the same campaign twice is a mis-click,
        # not a second attempt.
        idempotency_key=f"enquiry:{row.id}:{workflow.id}",
    )
    if row.lead_status in (None, LeadStatus.NEW.value):
        row.lead_status = LeadStatus.ENGAGED.value
    await session.flush()

    return EnquiryEnrolled(
        enquiry=_detail(row),
        run_id=str(run.id),
        contact_id=contact_id,
        created=created,
    )


@router.get("/{enquiry_id}", response_model=EnquiryDetail)
@limiter.limit(RATE_READ)
async def get_enquiry(
    request: Request,
    enquiry_id: str,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquiryDetail:
    row = await _load(session, enquiry_id, _institution_id(current_user))
    return _detail(row)


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
        if data.status not in {s.value for s in LeadStatus}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown status")
        row.lead_status = data.status
    if data.notes is not None:
        # Empty string clears; None means "not supplied", so a status-only
        # update cannot silently wipe somebody's notes.
        row.notes = data.notes or None
    await session.flush()
    return _detail(row)


def _detail(row: Contact) -> EnquiryDetail:
    return EnquiryDetail(
        **_item(row).model_dump(),
        notes=row.notes,
        attribution=row.attribution,
        external_ref=row.external_ref,
        intake_key=row.intake_key or "",
        location_id=None,
    )


async def _load(session: AsyncSession, enquiry_id: str, institution_id: str):
    row = (
        await session.execute(
            select(Contact).where(
                Contact.id == enquiry_id,
                Contact.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row
