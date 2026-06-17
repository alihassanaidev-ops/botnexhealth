"""
Contacts (patients) routes — institution-facing patient directory.

Primarily for no-PMS (call-intelligence-only) tenants, which have no PMS
patient record: callers are captured as Contacts, auto-matched on phone + name
(see PostCallService). This directory lists those patients (one row per primary
contact), shows their call history, and lets staff manually merge/unmerge
records that auto-match couldn't resolve (same person on two phones, a name
typo, etc.).

Merge is non-destructive: an absorbed contact becomes an *alias*
(``merged_into_id`` points at the primary) and its Calls are never reassigned,
so unmerge is lossless. Phone numbers are masked; the full value is served only
via the audited reveal endpoint, mirroring the calls API.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, nullslast, or_, select
from sqlalchemy.orm import aliased, selectinload

from src.app.api.deps import (
    get_current_active_user,
    get_current_institution_or_location_admin,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.api.routes.calls import (
    _ensure_phi_reveal_allowed,
    _location_scope_id,
    _tags_from_db,
)
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.call import Call
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.user import User
from src.app.services.audit import log_audit_background, phi_reveal_audit
from src.app.services.sms_privacy import mask_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/contacts", tags=["Contacts"])


# ── Response models ───────────────────────────────────────────────────────────


class ContactCallSummary(BaseModel):
    id: str
    contact_id: str | None
    call_date: str | None
    call_time: str | None
    call_status: str | None
    call_tags: list[str]
    summary: str | None
    callback_resolved: bool
    created_at: str


class ContactAlias(BaseModel):
    id: str
    full_name: str | None
    phone_masked: str | None
    phone_reveal_available: bool = False


class ContactListItem(BaseModel):
    id: str
    full_name: str | None
    first_name: str | None
    last_name: str | None
    is_new_patient: bool
    # Callback number, masked to the last 4 digits. Full value via the audited
    # POST /{contact_id}/reveal/phone endpoint.
    phone_masked: str | None = None
    phone_reveal_available: bool = False
    call_count: int = 0
    last_call_at: str | None = None
    alias_count: int = 0
    created_at: str


class ContactsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ContactListItem]


class ContactDetail(BaseModel):
    id: str
    full_name: str | None
    first_name: str | None
    last_name: str | None
    is_new_patient: bool
    phone_masked: str | None = None
    phone_reveal_available: bool = False
    created_at: str
    aliases: list[ContactAlias] = []
    calls: list[ContactCallSummary] = []
    call_count: int = 0


class MergeRequest(BaseModel):
    alias_id: str


class PhoneRevealResponse(BaseModel):
    contact_id: str
    phone: str | None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _phone_fields(contact: Contact) -> tuple[str | None, bool]:
    """(masked phone, reveal_available) for a contact."""
    available = contact.phone_encrypted is not None
    masked = mask_phone(contact.phone) if available else None
    return masked, available


async def _location_contact_ids_subq(current_user: User):
    """Subquery of contact_ids visible to a location-scoped user, or None."""
    loc_id = _location_scope_id(current_user)
    if not loc_id:
        return None
    return select(ContactLocationAccess.contact_id).where(
        ContactLocationAccess.location_id == loc_id
    )


async def _scoped_contact(
    session,
    contact_id: str,
    current_user: User,
    *,
    with_calls: bool = False,
) -> Contact:
    """Load a contact the current institution/location user may access."""
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    stmt = select(Contact).where(
        Contact.id == contact_id,
        Contact.institution_id == current_user.institution_id,
    )
    if with_calls:
        stmt = stmt.options(selectinload(Contact.calls))
    contact = (await session.execute(stmt)).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")

    loc_id = _location_scope_id(current_user)
    if loc_id:
        has_access = (
            await session.execute(
                select(ContactLocationAccess.id).where(
                    ContactLocationAccess.contact_id == contact_id,
                    ContactLocationAccess.location_id == loc_id,
                )
            )
        ).scalar_one_or_none()
        if not has_access:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return contact


# ── List patients ─────────────────────────────────────────────────────────────


@router.get("", response_model=ContactsListResponse)
@limiter.limit(RATE_READ)
async def list_contacts(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = Query(
        None,
        max_length=64,
        pattern=r"^[^%_]*$",
        description="Filter by patient name (partial, case-insensitive). Wildcards not allowed.",
    ),
) -> ContactsListResponse:
    """List patients (primary contacts) for the authenticated institution.

    Aggregates call counts and last-call timestamp across each primary contact
    and all of its merged aliases.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        # Correlated aggregates over each primary contact's member set
        # (the contact itself + any aliases that point at it).
        member = aliased(Contact)
        member_ids = (
            select(member.id)
            .where(or_(member.id == Contact.id, member.merged_into_id == Contact.id))
            .correlate(Contact)
        )
        call_count_expr = (
            select(func.count(Call.id))
            .where(Call.contact_id.in_(member_ids))
            .correlate(Contact)
            .scalar_subquery()
        )
        last_call_expr = (
            select(func.max(Call.created_at))
            .where(Call.contact_id.in_(member_ids))
            .correlate(Contact)
            .scalar_subquery()
        )
        alias_count_expr = (
            select(func.count(member.id))
            .where(member.merged_into_id == Contact.id)
            .correlate(Contact)
            .scalar_subquery()
        )

        base_filters = [
            Contact.institution_id == current_user.institution_id,
            # Primary contacts only — aliases roll up into their primary.
            Contact.merged_into_id.is_(None),
        ]
        loc_subq = await _location_contact_ids_subq(current_user)
        if loc_subq is not None:
            base_filters.append(Contact.id.in_(loc_subq))
        if search:
            base_filters.append(Contact.full_name.ilike(f"%{search}%"))

        total = (
            await session.execute(
                select(func.count()).select_from(Contact).where(*base_filters)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(
                    Contact,
                    call_count_expr.label("call_count"),
                    last_call_expr.label("last_call_at"),
                    alias_count_expr.label("alias_count"),
                )
                .where(*base_filters)
                .order_by(nullslast(desc(last_call_expr)), desc(Contact.created_at))
                .limit(limit)
                .offset(offset)
            )
        ).all()

        items: list[ContactListItem] = []
        for contact, call_count, last_call_at, alias_count in rows:
            masked, available = _phone_fields(contact)
            items.append(
                ContactListItem(
                    id=contact.id,
                    full_name=contact.full_name,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    is_new_patient=contact.is_new_patient,
                    phone_masked=masked,
                    phone_reveal_available=available,
                    call_count=int(call_count or 0),
                    last_call_at=last_call_at.isoformat() if last_call_at else None,
                    alias_count=int(alias_count or 0),
                    created_at=contact.created_at.isoformat(),
                )
            )

        return ContactsListResponse(total=total, limit=limit, offset=offset, items=items)


# ── Patient detail ────────────────────────────────────────────────────────────


async def _load_contact_detail(contact_id: str, current_user: User) -> ContactDetail:
    """Build the ContactDetail for a scoped contact (shared by GET + merge/unmerge)."""
    async with get_db_session() as session:
        contact = await _scoped_contact(session, contact_id, current_user)

        aliases = (
            (
                await session.execute(
                    select(Contact).where(Contact.merged_into_id == contact.id)
                )
            )
            .scalars()
            .all()
        )
        member_ids = [contact.id, *[a.id for a in aliases]]

        calls = (
            (
                await session.execute(
                    select(Call)
                    .where(Call.contact_id.in_(member_ids))
                    .order_by(desc(Call.created_at))
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )

        masked, available = _phone_fields(contact)
        alias_out: list[ContactAlias] = []
        for a in aliases:
            a_masked, a_available = _phone_fields(a)
            alias_out.append(
                ContactAlias(
                    id=a.id,
                    full_name=a.full_name,
                    phone_masked=a_masked,
                    phone_reveal_available=a_available,
                )
            )

        call_out = [
            ContactCallSummary(
                id=c.id,
                contact_id=c.contact_id,
                call_date=c.call_date.isoformat() if c.call_date else None,
                call_time=str(c.call_time) if c.call_time else None,
                call_status=c.call_status,
                call_tags=_tags_from_db(c.call_tags),
                summary=c.summary,
                callback_resolved=c.callback_resolved,
                created_at=c.created_at.isoformat(),
            )
            for c in calls
        ]

        return ContactDetail(
            id=contact.id,
            full_name=contact.full_name,
            first_name=contact.first_name,
            last_name=contact.last_name,
            is_new_patient=contact.is_new_patient,
            phone_masked=masked,
            phone_reveal_available=available,
            created_at=contact.created_at.isoformat(),
            aliases=alias_out,
            calls=call_out,
            call_count=len(call_out),
        )


@router.get("/{contact_id}", response_model=ContactDetail)
@limiter.limit(RATE_READ)
async def get_contact(
    request: Request,
    contact_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ContactDetail:
    """Patient detail: identity, linked aliases, and the union of their calls."""
    return await _load_contact_detail(contact_id, current_user)


# ── Reveal phone (audited) ────────────────────────────────────────────────────


@router.post("/{contact_id}/reveal/phone", response_model=PhoneRevealResponse)
@limiter.limit(RATE_READ)
async def reveal_contact_phone(
    request: Request,
    contact_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PhoneRevealResponse:
    """Reveal a patient's full phone number for a scoped clinic user, audited.

    Clinic users in the circle of care can reveal the full number (to call a
    patient back); platform SUPER_ADMIN is blocked (break-glass required).
    """
    _ensure_phi_reveal_allowed(
        current_user,
        action=AuditAction.VIEW_FULL_PHONE,
        target_resource=f"contact:{contact_id}/phone",
    )
    async with get_db_session() as session:
        contact = await _scoped_contact(session, contact_id, current_user)
        async with phi_reveal_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.VIEW_FULL_PHONE,
            target_resource=f"contact:{contact_id}/phone",
            institution_id=current_user.institution_id,
            user_id=str(current_user.id),
            location_id=current_user.location_id,
            metadata={"actor_role": current_user.role, "contact_id": contact_id},
        ):
            return PhoneRevealResponse(contact_id=contact.id, phone=contact.phone)


# ── Merge / unmerge ───────────────────────────────────────────────────────────


@router.post("/{contact_id}/merge", response_model=ContactDetail)
@limiter.limit(RATE_WRITE)
async def merge_contact(
    request: Request,
    contact_id: str,
    body: MergeRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> ContactDetail:
    """Merge ``alias_id`` into the primary contact ``contact_id``.

    Non-destructive: the alias keeps its Calls and simply points at the primary
    (``merged_into_id``). Both must currently be standalone primaries, and the
    absorbed contact must not itself have aliases (unmerge those first) — this
    keeps unmerge exactly reversible.
    """
    if body.alias_id == contact_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot merge a contact into itself")

    async with get_db_session() as session:
        primary = await _scoped_contact(session, contact_id, current_user)
        alias = await _scoped_contact(session, body.alias_id, current_user)

        if primary.merged_into_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Target is itself merged into another contact; merge into the primary instead.",
            )
        if alias.merged_into_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This contact is already merged into another patient.",
            )
        alias_has_children = (
            await session.execute(
                select(Contact.id).where(Contact.merged_into_id == alias.id).limit(1)
            )
        ).scalar_one_or_none()
        if alias_has_children:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This contact has its own linked records; unmerge those first.",
            )

        alias.merged_into_id = primary.id
        await session.commit()

    log_audit_background(
        actor=AuditActor.ADMIN,
        action=AuditAction.CONTACT_MERGE,
        target_resource=f"contact:{contact_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role, "alias_id": body.alias_id},
        institution_id=current_user.institution_id,
        user_id=str(current_user.id),
        location_id=current_user.location_id,
    )

    return await _load_contact_detail(contact_id, current_user)


@router.post("/{contact_id}/unmerge", response_model=ContactDetail)
@limiter.limit(RATE_WRITE)
async def unmerge_contact(
    request: Request,
    contact_id: str,
    body: MergeRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> ContactDetail:
    """Detach ``alias_id`` from the primary ``contact_id``; it becomes standalone again."""
    async with get_db_session() as session:
        # Scope both so cross-institution ids can't be probed.
        await _scoped_contact(session, contact_id, current_user)
        alias = await _scoped_contact(session, body.alias_id, current_user)

        if alias.merged_into_id != contact_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That contact is not merged into this patient.",
            )
        alias.merged_into_id = None
        await session.commit()

    log_audit_background(
        actor=AuditActor.ADMIN,
        action=AuditAction.CONTACT_UNMERGE,
        target_resource=f"contact:{contact_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"actor_role": current_user.role, "alias_id": body.alias_id},
        institution_id=current_user.institution_id,
        user_id=str(current_user.id),
        location_id=current_user.location_id,
    )

    return await _load_contact_detail(contact_id, current_user)
