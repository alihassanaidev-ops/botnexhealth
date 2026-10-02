"""Managing the intake endpoints a clinic gives to its forms.

Each row is one form's credential. A practice typically runs several — the
website enquiry form, a campaign landing page, a paid-ads form — and needs to
retire one without disturbing the rest, which is why these are per form rather
than one per clinic.

The token is returned **once**, at creation or rotation, and never again. It is
stored only as a keyed hash, so there is nothing to return later even to the
person who made it. That is deliberate: the row is what an attacker reaches
first, and a credential recoverable from a backup is a live intake endpoint for
that clinic. The cost is that a lost token must be rotated rather than looked
up, and the UI has to say so plainly at the moment it is shown.

``last_used_at`` exists because a form that has quietly stopped posting is
otherwise invisible — a clinic notices a broken integration when leads dry up,
which is far too late.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session_dep
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.enquiry_intake_source import (
    EnquiryIntakeSource,
    generate_intake_token,
    hash_intake_token,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User
from src.app.services.audit_decorator import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/enquiry-sources", tags=["Enquiry Intake"])

_InstitutionUser = Annotated[User, Depends(get_current_institution_admin)]
_Session = Annotated[AsyncSession, Depends(get_db_session_dep)]


class EnquirySourceCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    #: Which location the leads belong to. Null lets a single-location practice
    #: skip a choice that has only one answer.
    location_id: str | None = None
    #: Recorded on every enquiry from this form.
    source_name: str = Field(default="external_form", max_length=80)
    default_attribution: dict | None = None
    #: When set, posts must carry a matching body HMAC. Worth it for a provider
    #: that can sign; a URL token alone cannot prove the body.
    signing_secret: str | None = Field(default=None, max_length=200)


class EnquirySourceUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    default_attribution: dict | None = None


class EnquirySourceResponse(BaseModel):
    id: str
    label: str
    location_id: str | None
    source_name: str
    is_active: bool
    has_signing_secret: bool
    default_attribution: dict | None
    created_at: datetime
    last_used_at: datetime | None


class EnquirySourceCreated(EnquirySourceResponse):
    """The only response that ever carries the token."""

    #: Shown once. There is no way to retrieve it afterwards — only rotate.
    token: str
    #: The full URL to paste into the form provider, so nobody has to assemble
    #: it from a token and a hostname and get it subtly wrong.
    intake_url: str


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No institution")
    return str(user.institution_id)


def _to_response(row: EnquiryIntakeSource) -> EnquirySourceResponse:
    return EnquirySourceResponse(
        id=str(row.id),
        label=row.label,
        location_id=str(row.location_id) if row.location_id else None,
        source_name=row.source_name,
        is_active=row.is_active,
        has_signing_secret=bool(row.signing_secret_encrypted),
        default_attribution=row.default_attribution,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


def _intake_url(token: str) -> str:
    from src.app.config import settings

    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/api/enquiries/intake/{token}"


async def _load(session: AsyncSession, source_id: str, institution_id: str):
    row = (
        await session.execute(
            select(EnquiryIntakeSource).where(
                EnquiryIntakeSource.id == source_id,
                EnquiryIntakeSource.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.get("", response_model=list[EnquirySourceResponse])
@limiter.limit(RATE_READ)
async def list_sources(
    request: Request,
    current_user: _InstitutionUser,
    session: _Session,
) -> list[EnquirySourceResponse]:
    """Every form this clinic has issued a credential to. Never the tokens."""
    rows = (
        (
            await session.execute(
                select(EnquiryIntakeSource)
                .where(EnquiryIntakeSource.institution_id == _institution_id(current_user))
                .order_by(EnquiryIntakeSource.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(row) for row in rows]


@router.post("", response_model=EnquirySourceCreated, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_CREATE,
    resource=lambda *a, **kw: f"enquiry_intake_source:create:{getattr(kw.get('data'), 'label', '?')}",
    actor=AuditActor.ADMIN,
)
async def create_source(
    request: Request,
    data: EnquirySourceCreate,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquirySourceCreated:
    """Issue a credential for one form. The token is shown here and nowhere else."""
    institution_id = _institution_id(current_user)

    if data.location_id:
        # A token that points at another clinic's location would land leads in
        # the wrong tenant, so the location is checked rather than trusted.
        owns = (
            await session.execute(
                select(InstitutionLocation.id).where(
                    InstitutionLocation.id == data.location_id,
                    InstitutionLocation.institution_id == institution_id,
                )
            )
        ).scalar_one_or_none()
        if owns is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")

    token = generate_intake_token()
    row = EnquiryIntakeSource(
        institution_id=institution_id,
        location_id=data.location_id,
        label=data.label,
        token_hash=hash_intake_token(token),
        source_name=data.source_name,
        default_attribution=data.default_attribution,
        is_active=True,
    )
    if data.signing_secret:
        row.signing_secret = data.signing_secret
    session.add(row)
    await session.flush()

    base = _to_response(row)
    return EnquirySourceCreated(
        **base.model_dump(), token=token, intake_url=_intake_url(token)
    )


@router.patch("/{source_id}", response_model=EnquirySourceResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"enquiry_intake_source:update:{kw.get('source_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def update_source(
    request: Request,
    source_id: str,
    data: EnquirySourceUpdate,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquirySourceResponse:
    """Rename, or switch a form off. Revoking is deliberately reversible: a
    clinic that kills the wrong one should not have to reconfigure a provider."""
    row = await _load(session, source_id, _institution_id(current_user))
    if data.label is not None:
        row.label = data.label
    if data.is_active is not None:
        row.is_active = data.is_active
    if data.default_attribution is not None:
        row.default_attribution = data.default_attribution
    await session.flush()
    return _to_response(row)


@router.post("/{source_id}/rotate", response_model=EnquirySourceCreated)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"enquiry_intake_source:rotate:{kw.get('source_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def rotate_source(
    request: Request,
    source_id: str,
    current_user: _InstitutionUser,
    session: _Session,
) -> EnquirySourceCreated:
    """Replace the token. The old one stops working immediately.

    The only remedy for a token that leaked or was lost, since nothing can hand
    the original back.
    """
    row = await _load(session, source_id, _institution_id(current_user))
    token = generate_intake_token()
    row.token_hash = hash_intake_token(token)
    await session.flush()
    base = _to_response(row)
    return EnquirySourceCreated(
        **base.model_dump(), token=token, intake_url=_intake_url(token)
    )
