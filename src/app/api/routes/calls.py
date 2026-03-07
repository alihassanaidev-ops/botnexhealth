"""
Calls routes — institution-facing API for browsing call records.

All endpoints are institution-scoped: a user can only see calls belonging
to their own institution. PHI fields (transcript, recording_url) are
intentionally excluded from the list response but available via the
detail endpoint.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, nullslast, or_, select
from sqlalchemy.orm import selectinload

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.call import Call
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/calls", tags=["Calls"])


# ── Response models ───────────────────────────────────────────────────────────


class ContactSummary(BaseModel):
    id: str
    full_name: str | None
    first_name: str | None
    last_name: str | None


class CallRecord(BaseModel):
    id: str
    call_direction: str | None
    call_status: str | None
    call_tags: list[str]          # all normalized tags for this call
    patient_status: str | None
    summary: str | None
    patient_sentiment: str | None
    next_action: str | None
    is_new_patient: bool
    is_complaint: bool
    is_insurance_billing: bool
    call_date: date | None
    call_time: str | None
    call_duration_seconds: int | None
    callback_resolved: bool
    created_at: str
    contact: ContactSummary | None


class CustomFieldValueOut(BaseModel):
    """A single custom field value for a call."""
    field_key: str
    field_name: str
    field_type: str
    value: str | None
    is_phi: bool
    display_order: int


class CallDetail(CallRecord):
    """Extended call record that includes PHI fields for the detail view."""
    # Raw plain-text transcript (may contain PHI — kept for internal audit)
    transcript: str | None
    # Structured JSONB turn-by-turn transcript arrays
    transcript_with_tool_calls: list[dict] | None       # full unredacted
    scrubbed_transcript_with_tool_calls: list[dict] | None  # PII-scrubbed (default UI)
    recording_url: str | None
    custom_fields: list[CustomFieldValueOut] = []


class CallsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CallRecord]


class ResolveCallbackRequest(BaseModel):
    note: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tags_from_db(call_tags_str: str | None) -> list[str]:
    """Convert the comma-separated call_tags DB string to a list."""
    if not call_tags_str:
        return []
    return [t.strip() for t in call_tags_str.split(",") if t.strip()]


def _call_to_record(call: Call) -> CallRecord:
    contact_out: ContactSummary | None = None
    if call.contact:
        contact_out = ContactSummary(
            id=call.contact.id,
            full_name=call.contact.full_name,
            first_name=call.contact.first_name,
            last_name=call.contact.last_name,
        )
    return CallRecord(
        id=call.id,
        call_direction=call.call_direction,
        call_status=call.call_status,
        call_tags=_tags_from_db(call.call_tags),
        patient_status=call.patient_status,
        summary=call.summary,
        patient_sentiment=call.patient_sentiment,
        next_action=call.next_action,
        is_new_patient=call.is_new_patient,
        is_complaint=call.is_complaint,
        is_insurance_billing=call.is_insurance_billing,
        call_date=call.call_date,
        call_time=str(call.call_time) if call.call_time else None,
        call_duration_seconds=call.call_duration_seconds,
        callback_resolved=call.callback_resolved,
        created_at=call.created_at.isoformat(),
        contact=contact_out,
    )


async def _location_agent_filter(session, current_user: User) -> str | None:
    """
    For location-scoped roles, return the mapped Retell agent id to enforce call visibility.
    """
    if current_user.role not in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        return None
    if not current_user.location_id or not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Location assignment required")

    location_result = await session.execute(
        select(InstitutionLocation).where(
            InstitutionLocation.id == current_user.location_id,
            InstitutionLocation.institution_id == current_user.institution_id,
        )
    )
    location = location_result.scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assigned location not found")
    if not location.retell_agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assigned location has no agent mapping")
    return location.retell_agent_id


# ── List calls ────────────────────────────────────────────────────────────────


@router.get("", response_model=CallsListResponse)
@limiter.limit(RATE_READ)
async def list_calls(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    # Single-tag shorthand: ?status=complaint
    call_status: str | None = Query(None, alias="status"),
    # Multi-tag filter: ?tags=complaint&tags=faq_handled
    tags: list[str] = Query(default=[]),
    direction: str | None = Query(None),
    search: str | None = Query(None, description="Filter by contact name or phone (partial, case-insensitive)"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> CallsListResponse:
    """
    List calls for the authenticated institution.

    Supports filtering by status/tags, direction, date range, and contact
    name/phone search. Returns paginated results ordered newest-first.
    PHI fields (transcript, recording_url) are excluded — use GET /{id} for those.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    # Merge ?status= and ?tags= into one list
    active_tags = list(tags)
    if call_status and call_status not in active_tags:
        active_tags.append(call_status)

    async with get_db_session() as session:
        conditions = [Call.institution_id == current_user.institution_id]
        location_agent_id = await _location_agent_filter(session, current_user)
        if location_agent_id:
            conditions.append(Call.agent_used == location_agent_id)

        # Tag filtering: each tag must appear in call_tags (or match call_status)
        for tag in active_tags:
            conditions.append(
                or_(
                    Call.call_status == tag,
                    Call.call_tags.ilike(f"%{tag}%"),
                )
            )

        if direction:
            conditions.append(Call.call_direction == direction)
        if date_from:
            conditions.append(Call.call_date >= date_from)
        if date_to:
            conditions.append(Call.call_date <= date_to)

        need_join = bool(search)

        def _with_search(q):
            if need_join:
                q = q.join(Contact, Call.contact_id == Contact.id, isouter=True)
                q = q.where(
                    or_(
                        Contact.full_name.ilike(f"%{search}%"),
                        Contact.phone_hash.isnot(None),  # joined — phone filter via app layer below
                    )
                )
            return q

        # For phone search we do an app-layer pre-filter instead of DB ILIKE on encrypted data:
        # We just search contact name here; phone search would require decrypting all contacts.
        # A cleaner approach: search by name only, and callers can use date/tag filters for narrowing.
        def _with_name_search(q):
            if not search:
                return q
            q = q.join(Contact, Call.contact_id == Contact.id, isouter=True)
            q = q.where(Contact.full_name.ilike(f"%{search}%"))
            return q

        total: int = (
            await session.execute(
                _with_name_search(select(func.count(Call.id)).where(*conditions))
            )
        ).scalar_one()

        rows = (
            await session.execute(
                _with_name_search(
                    select(Call)
                    .where(*conditions)
                    .options(selectinload(Call.contact))
                    .order_by(nullslast(desc(Call.call_date)), desc(Call.created_at))
                    .limit(limit)
                    .offset(offset)
                )
            )
        ).scalars().all()

        response = CallsListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_call_to_record(c) for c in rows],
        )
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_CALLS,
            target_resource="calls:list",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
                "result_count": len(response.items),
            },
            institution_id=current_user.institution_id,
        )
        return response


# ── Call detail ───────────────────────────────────────────────────────────────


@router.get("/{call_id}", response_model=CallDetail)
@limiter.limit(RATE_READ)
async def get_call(
    request: Request,
    call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CallDetail:
    """
    Get full detail for a single call including transcript and recording URL.

    PHI note: transcript and recording_url may contain patient health information.
    Access is restricted to authenticated institution users for their own institution only.
    Vendor-specific identifiers are intentionally excluded from this response.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        conditions = [Call.id == call_id, Call.institution_id == current_user.institution_id]
        location_agent_id = await _location_agent_filter(session, current_user)
        if location_agent_id:
            conditions.append(Call.agent_used == location_agent_id)

        call = (
            await session.execute(
                select(Call)
                .where(*conditions)
                .options(selectinload(Call.contact))
            )
        ).scalar_one_or_none()

        if not call:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")

        # Load custom field values
        from src.app.services.custom_field_service import CustomFieldService

        cf_svc = CustomFieldService(session)
        cf_pairs = await cf_svc.get_values_for_entity(
            current_user.institution_id, "call", call.id,
        )
        custom_fields = [
            CustomFieldValueOut(
                field_key=defn.field_key,
                field_name=defn.field_name,
                field_type=defn.field_type,
                value=val.get_value(is_phi=defn.is_phi),
                is_phi=defn.is_phi,
                display_order=defn.display_order,
            )
            for defn, val in cf_pairs
        ]

        base = _call_to_record(call)
        response = CallDetail(
            **base.model_dump(),
            transcript=call.transcript,
            transcript_with_tool_calls=call.transcript_with_tool_calls,
            scrubbed_transcript_with_tool_calls=call.scrubbed_transcript_with_tool_calls,
            recording_url=call.recording_url,
            custom_fields=custom_fields,
        )
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_CALL_DETAIL,
            target_resource=f"call:{call.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
            },
            institution_id=current_user.institution_id,
        )
        return response


# ── Resolve callback ──────────────────────────────────────────────────────────


@router.patch("/{call_id}/resolve", response_model=CallRecord)
@limiter.limit(RATE_WRITE)
async def resolve_callback(
    request: Request,
    call_id: str,
    body: ResolveCallbackRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CallRecord:
    """
    Mark a callback call as resolved and optionally record a resolution note.

    Idempotent: resolving an already-resolved call updates the note if provided.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        conditions = [Call.id == call_id, Call.institution_id == current_user.institution_id]
        location_agent_id = await _location_agent_filter(session, current_user)
        if location_agent_id:
            conditions.append(Call.agent_used == location_agent_id)

        call = (
            await session.execute(
                select(Call)
                .where(*conditions)
                .options(selectinload(Call.contact))
            )
        ).scalar_one_or_none()

        if not call:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")

        call.callback_resolved = True
        call.callback_resolved_at = datetime.now(timezone.utc)
        if body.note is not None:
            call.callback_note = body.note.strip() or None

        await session.commit()
        await session.refresh(call)

        logger.info("Callback resolved: call=%s institution=%s", call_id, current_user.institution_id)
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"call:{call.id}/callback",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
                "note_set": body.note is not None,
            },
            institution_id=current_user.institution_id,
        )
        return _call_to_record(call)
