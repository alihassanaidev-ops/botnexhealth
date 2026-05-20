"""
Calls routes — institution-facing API for browsing call records.

All endpoints are institution-scoped: a user can only see calls belonging
to their own institution/location scope. Clinic users may see call summaries
needed for care operations; transcript bodies, recordings, and custom PHI
fields require explicit audited reveal endpoints.
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
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.call import Call
from src.app.models.contact import Contact
from src.app.models.custom_field import EntityType
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background, phi_reveal_audit
from src.app.services.custom_field_service import CustomFieldService
from src.app.services.event_bus import publish_event
from src.app.services.sms_privacy import hash_for_logging, safe_error_summary

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
    call_tags: list[str]  # all normalized tags for this call
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
    value_masked: bool = False
    reveal_available: bool = False
    display_order: int


class CallDetail(CallRecord):
    """Extended call record. Transcript and recording bodies are revealed
    only via the audited reveal endpoints — the detail response carries
    only availability flags and scoped metadata.
    """

    transcript_available: bool = False
    recording_available: bool = False
    custom_fields: list[CustomFieldValueOut] = []


class CallsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CallRecord]


class ResolveCallbackRequest(BaseModel):
    note: str | None = None


class TranscriptRevealResponse(BaseModel):
    call_id: str
    transcript_with_tool_calls: list[dict] | None


class RecordingRevealResponse(BaseModel):
    call_id: str
    recording_url: str | None


class CustomFieldRevealResponse(BaseModel):
    call_id: str
    field_key: str
    value: str | None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tags_from_db(call_tags_str: str | None) -> list[str]:
    """Convert the comma-separated call_tags DB string to a list."""
    if not call_tags_str:
        return []
    return [t.strip() for t in call_tags_str.split(",") if t.strip()]


def _mask_name(name: str | None) -> str | None:
    """Partially redact a name, keeping first and last characters visible.

    Examples:
        ``"Sarah"``  → ``"S***h"``
        ``"Loomer"`` → ``"L****r"``
        ``"Jo"``     → ``"J*"``
        ``"A"``      → ``"A"``
    """
    if not name:
        return None
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def _mask_full_name(full_name: str | None) -> str | None:
    """Mask each word in a full name independently."""
    if not full_name:
        return None
    return " ".join(_mask_name(part) for part in full_name.split())


def _call_to_record(call: Call, *, redact_phi: bool = True) -> CallRecord:
    """Convert a Call ORM object to the API response model.

    Args:
        redact_phi: When True (default), patient names are partially masked
            (e.g. ``Sarah Loomer`` → ``S***h L****r``) so the list endpoint
            never exposes full PHI. The detail endpoint passes
            ``redact_phi=False`` for authorised roles.
    """
    contact_out: ContactSummary | None = None
    if call.contact:
        if redact_phi:
            contact_out = ContactSummary(
                id=call.contact.id,
                full_name=_mask_full_name(call.contact.full_name),
                first_name=_mask_name(call.contact.first_name),
                last_name=_mask_name(call.contact.last_name),
            )
        else:
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


def _location_scope_id(current_user: User) -> str | None:
    """For LOCATION_ADMIN / STAFF, return the location_id to filter by.

    Returns None for INSTITUTION_ADMIN (no per-location filter — they see
    all calls in their institution).

    Replaces the legacy `_location_agent_filter` that did a string match
    against InstitutionLocation.retell_agent_id. Calls now have a direct
    ``location_id`` foreign key (set at webhook time via the agent_id →
    location mapping) so we can scope authoritatively without a roundtrip.
    """
    if current_user.role not in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        return None
    if not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Location assignment required",
        )
    return str(current_user.location_id)


# Back-compat shim: callbacks.py imports this name. Keep it as a sync helper
# returning the location_id (string) — callers should pair with
# `Call.location_id == <id>`, not `Call.agent_used == <id>`.
async def _location_agent_filter(session, current_user: User) -> str | None:  # noqa: ARG001
    """Deprecated alias — returns the location_id filter for LOCATION_ADMIN/STAFF."""
    return _location_scope_id(current_user)


async def _get_scoped_call(
    session,
    call_id: str,
    current_user: User,
    *,
    audit_on_miss: AuditAction | None = None,
) -> Call:
    """Load a call the current institution/location user is allowed to access."""
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution"
        )

    conditions = [
        Call.id == call_id,
        Call.institution_id == current_user.institution_id,
    ]
    location_id = _location_scope_id(current_user)
    if location_id:
        conditions.append(Call.location_id == location_id)

    call = (
        await session.execute(
            select(Call).where(*conditions).options(selectinload(Call.contact))
        )
    ).scalar_one_or_none()

    if not call:
        if audit_on_miss is not None:
            log_audit_background(
                actor=AuditActor.ADMIN,
                action=audit_on_miss,
                target_resource=f"call:{call_id}",
                outcome=AuditOutcome.FAILURE_NOT_FOUND,
                metadata={
                    "actor_role": current_user.role,
                    "reason": "call_not_in_user_scope",
                },
                institution_id=current_user.institution_id,
                user_id=str(current_user.id),
                location_id=current_user.location_id,
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Call not found"
        )
    return call


def _ensure_phi_reveal_allowed(
    current_user: User,
    *,
    action: AuditAction,
    target_resource: str,
) -> None:
    """Block platform-level users from revealing clinic PHI without break-glass.

    Records a FAILURE_UNAUTHORIZED audit row before raising so that
    HIPAA §164.312(b) reviews can detect probing attempts. The denial
    is itself a security-relevant event — leaving it un-audited would be
    a gap.
    """
    if current_user.role == UserRole.SUPER_ADMIN.value:
        log_audit_background(
            actor=AuditActor.ADMIN,
            action=action,
            target_resource=target_resource,
            outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
            metadata={
                "actor_role": current_user.role,
                "reason": "super_admin_phi_reveal_blocked",
            },
            institution_id=current_user.institution_id,
            user_id=str(current_user.id),
            location_id=current_user.location_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-admin PHI reveal requires a break-glass workflow",
        )


def _phi_reveal_audit_for_call(
    current_user: User,
    call: Call,
    *,
    action: AuditAction,
    target_suffix: str,
    extra_metadata: dict | None = None,
):
    """Two-row pre-then-post audit context for PHI-reveal endpoints.

    Use as ``async with _phi_reveal_audit_for_call(...): return Response(...)``.
    The body must build the response that decrypts PHI; the context
    manager writes INITIATED before the body runs (fail-closed: if the
    audit write fails, no PHI is decrypted) and SUCCESS / FAILURE after.
    """
    metadata = {
        "actor_role": current_user.role,
        "institution_id": current_user.institution_id,
        "location_id": current_user.location_id,
        "contact_id": call.contact_id,
        **(extra_metadata or {}),
    }
    return phi_reveal_audit(
        actor=AuditActor.ADMIN,
        action=action,
        target_resource=f"call:{call.id}/{target_suffix}",
        institution_id=current_user.institution_id,
        user_id=str(current_user.id),
        location_id=current_user.location_id,
        metadata=metadata,
    )


def _custom_field_response(defn, val, *, reveal: bool = False) -> CustomFieldValueOut:
    if defn.is_phi and not reveal:
        value_available = getattr(val, "value_encrypted", None) is not None
        if not hasattr(val, "value_encrypted") and not hasattr(val, "value_text"):
            value_available = True
        return CustomFieldValueOut(
            field_key=defn.field_key,
            field_name=defn.field_name,
            field_type=defn.field_type,
            value=None,
            is_phi=defn.is_phi,
            value_masked=True,
            reveal_available=value_available,
            display_order=defn.display_order,
        )
    raw_value = val.get_value(is_phi=defn.is_phi)
    return CustomFieldValueOut(
        field_key=defn.field_key,
        field_name=defn.field_name,
        field_type=defn.field_type,
        value=raw_value,
        is_phi=defn.is_phi,
        value_masked=False,
        reveal_available=False,
        display_order=defn.display_order,
    )


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
    search: str | None = Query(
        None,
        max_length=64,
        pattern=r"^[^%_]*$",
        description="Filter by contact name (partial, case-insensitive). Wildcards not allowed.",
    ),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> CallsListResponse:
    """
    List calls for the authenticated institution.

    Supports filtering by status/tags, direction, date range, and contact
    name/phone search. Returns paginated results ordered newest-first.
    Clinic users see the call summary/next action needed for care operations.
    Transcript bodies, recording URLs, and custom PHI fields are excluded from
    the list response and require explicit audited reveal endpoints.
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
            conditions.append(Call.location_id == location_agent_id)

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
                        Contact.phone_hash.isnot(
                            None
                        ),  # joined — phone filter via app layer below
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
            (
                await session.execute(
                    _with_name_search(
                        select(Call)
                        .where(*conditions)
                        .options(selectinload(Call.contact))
                        .order_by(
                            nullslast(desc(Call.call_date)), desc(Call.created_at)
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
            )
            .scalars()
            .all()
        )

        items = [_call_to_record(c, redact_phi=True) for c in rows]
        response = CallsListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=items,
        )
        contact_id_hashes = [
            hash_for_logging(c.contact_id) for c in rows if c.contact_id
        ]
        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
            action=AuditAction.VIEW_CALLS,
            target_resource="calls:list",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
                "result_count": len(response.items),
                "contact_id_hashes": contact_id_hashes,
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
    Get scrubbed detail for a single call.

    PHI note: full transcript, raw transcript, recording URL, and PHI custom
    fields are not returned here. They are available through explicit audited
    reveal endpoints for clinic users in the circle of care.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution"
        )

    async with get_db_session() as session:
        call = await _get_scoped_call(session, call_id, current_user)

        # Load custom field values
        cf_svc = CustomFieldService(session)
        cf_pairs = await cf_svc.get_values_for_entity(
            current_user.institution_id,
            "call",
            call.id,
        )
        custom_fields = [_custom_field_response(defn, val) for defn, val in cf_pairs]

        # SUPER_ADMIN is platform-level and not in the circle of care — redact PHI.
        # All other institution-scoped roles may view patient names for care operations.
        redact = current_user.role == UserRole.SUPER_ADMIN.value
        base = _call_to_record(call, redact_phi=redact)

        response = CallDetail(
            **base.model_dump(),
            transcript_available=bool(call.transcript_with_tool_calls_encrypted),
            recording_available=bool(call.recording_url),
            custom_fields=custom_fields,
        )
        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
            action=AuditAction.VIEW_CALL_DETAIL,
            target_resource=f"call:{call.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
                "contact_id": call.contact_id,
            },
            institution_id=current_user.institution_id,
        )
        return response


@router.post("/{call_id}/reveal/transcript", response_model=TranscriptRevealResponse)
@limiter.limit(RATE_READ)
async def reveal_transcript(
    request: Request,
    call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> TranscriptRevealResponse:
    """Reveal the (scrubbed) structured transcript and audit the access.

    Only Retell's PII-scrubbed transcript is stored, so this single endpoint
    replaces the legacy raw/full split.
    """
    _ensure_phi_reveal_allowed(
        current_user,
        action=AuditAction.VIEW_FULL_TRANSCRIPT,
        target_resource=f"call:{call_id}/transcript",
    )
    async with get_db_session() as session:
        call = await _get_scoped_call(
            session,
            call_id,
            current_user,
            audit_on_miss=AuditAction.VIEW_FULL_TRANSCRIPT,
        )
        async with _phi_reveal_audit_for_call(
            current_user,
            call,
            action=AuditAction.VIEW_FULL_TRANSCRIPT,
            target_suffix="transcript",
        ):
            return TranscriptRevealResponse(
                call_id=call.id,
                transcript_with_tool_calls=call.transcript_with_tool_calls,
            )


@router.post("/{call_id}/reveal/recording", response_model=RecordingRevealResponse)
@limiter.limit(RATE_READ)
async def reveal_recording(
    request: Request,
    call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecordingRevealResponse:
    """Reveal a time-limited call recording URL for a scoped clinic user and audit it."""
    _ensure_phi_reveal_allowed(
        current_user,
        action=AuditAction.VIEW_CALL_RECORDING,
        target_resource=f"call:{call_id}/recording",
    )
    async with get_db_session() as session:
        call = await _get_scoped_call(
            session,
            call_id,
            current_user,
            audit_on_miss=AuditAction.VIEW_CALL_RECORDING,
        )
        async with _phi_reveal_audit_for_call(
            current_user,
            call,
            action=AuditAction.VIEW_CALL_RECORDING,
            target_suffix="recording",
        ):
            # Lazy import: boto3 is only loaded when a recording is actually revealed,
            # keeping it out of the import graph for hot paths and unit tests.
            from src.app.tasks.recordings import generate_presigned_url

            signed_recording_url = (
                generate_presigned_url(call.recording_url)
                if call.recording_url
                else None
            )
            return RecordingRevealResponse(
                call_id=call.id, recording_url=signed_recording_url
            )


@router.post(
    "/{call_id}/reveal/custom-fields/{field_key}",
    response_model=CustomFieldRevealResponse,
)
@limiter.limit(RATE_READ)
async def reveal_custom_phi_field(
    request: Request,
    call_id: str,
    field_key: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CustomFieldRevealResponse:
    """Reveal a single PHI custom field for a scoped clinic user and audit it."""
    _ensure_phi_reveal_allowed(
        current_user,
        action=AuditAction.VIEW_CUSTOM_PHI_FIELD,
        target_resource=f"call:{call_id}/custom-fields/{field_key}",
    )
    async with get_db_session() as session:
        call = await _get_scoped_call(
            session,
            call_id,
            current_user,
            audit_on_miss=AuditAction.VIEW_CUSTOM_PHI_FIELD,
        )

        cf_svc = CustomFieldService(session)
        cf_pairs = await cf_svc.get_values_for_entity(
            current_user.institution_id,
            EntityType.CALL.value,
            call.id,
        )
        for defn, val in cf_pairs:
            if defn.field_key != field_key:
                continue
            if not defn.is_phi:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Field is not PHI and does not require reveal",
                )
            async with _phi_reveal_audit_for_call(
                current_user,
                call,
                action=AuditAction.VIEW_CUSTOM_PHI_FIELD,
                target_suffix=f"custom-fields/{field_key}",
                extra_metadata={"field_key": field_key},
            ):
                return CustomFieldRevealResponse(
                    call_id=call.id,
                    field_key=field_key,
                    value=val.get_value(is_phi=True),
                )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found"
        )


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution"
        )

    async with get_db_session() as session:
        conditions = [
            Call.id == call_id,
            Call.institution_id == current_user.institution_id,
        ]
        location_agent_id = await _location_agent_filter(session, current_user)
        if location_agent_id:
            conditions.append(Call.location_id == location_agent_id)

        call = (
            await session.execute(
                select(Call).where(*conditions).options(selectinload(Call.contact))
            )
        ).scalar_one_or_none()

        if not call:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Call not found"
            )

        call.callback_resolved = True
        call.callback_resolved_at = datetime.now(timezone.utc)
        if body.note is not None:
            call.callback_note = body.note.strip() or None

        await session.commit()
        await session.refresh(call)

        logger.info(
            "Callback resolved: call_hash=%s institution_hash=%s",
            hash_for_logging(call_id),
            hash_for_logging(current_user.institution_id),
        )
        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
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

        try:
            publish_event(current_user.institution_id, "callbacks_updated")
            publish_event(current_user.institution_id, "dashboard_updated")
            publish_event(current_user.institution_id, "calls_updated")
        except Exception as exc:
            logger.warning(
                "Failed to publish callback-resolution SSE events: call_hash=%s institution_hash=%s error=%s",
                hash_for_logging(call_id),
                hash_for_logging(current_user.institution_id),
                safe_error_summary(exc),
            )
        return _call_to_record(call)
