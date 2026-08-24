"""Routes for a clinic's email sending identity.

Split by who should be able to do what:

* **Institution admins** see their own identities, edit the display name and
  reply-to, and re-check verification. That is the day-to-day surface.
* **Super admins** provision and remove them. Provisioning creates real AWS
  resources — an SES identity, a tenant, a configuration set, and DNS records —
  against capped quotas, so it belongs to onboarding rather than to a
  self-service button a clinic can hold down.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_institution_admin, get_current_super_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.email_sending_identity import EmailIdentityStatus
from src.app.models.user import User
from src.app.services.email.identity_service import EmailIdentityService
from src.app.services.email.ses_provisioning import SesProvisioningError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/institution/email-sending-identities",
    tags=["Email Sending Identities"],
)


class DnsRecordResponse(BaseModel):
    name: str
    type: str
    value: str


class EmailSendingIdentityResponse(BaseModel):
    id: str
    institution_id: str
    location_id: str | None
    provider: str
    domain: str
    from_address: str
    from_name: str | None
    reply_to_address: str | None
    status: str
    #: True only when the domain is verified and safe to send from.
    is_sendable: bool
    dns_records: list[DnsRecordResponse]
    #: True when we published the records ourselves, so the clinic has nothing
    #: to do. False means the records below must be published manually.
    dns_self_published: bool
    verified_at: str | None
    last_checked_at: str | None
    failure_reason: str | None


class EmailSendingIdentityListResponse(BaseModel):
    identities: list[EmailSendingIdentityResponse]


class ProvisionRequest(BaseModel):
    institution_id: str
    #: Omit for the institution-wide identity.
    location_id: str | None = None
    from_name: str | None = Field(default=None, max_length=255)
    reply_to_address: str | None = Field(default=None, max_length=320)
    local_part: str = Field(default="hello", max_length=64, pattern=r"^[a-z0-9._-]+$")


class IdentityUpdateRequest(BaseModel):
    """Display fields only. The domain and address are immutable — changing them
    would mean re-verifying, which is a provisioning operation."""

    from_name: str | None = Field(default=None, max_length=255)
    reply_to_address: str | None = Field(default=None, max_length=320)


def _require_institution(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution"
        )
    return user.institution_id


def _to_response(identity: Any) -> EmailSendingIdentityResponse:
    records = [DnsRecordResponse(**r) for r in (identity.dns_records or [])]
    return EmailSendingIdentityResponse(
        id=str(identity.id),
        institution_id=str(identity.institution_id),
        location_id=str(identity.location_id) if identity.location_id else None,
        provider=identity.provider,
        domain=identity.domain,
        from_address=identity.from_address,
        from_name=identity.from_name,
        reply_to_address=identity.reply_to_address,
        status=identity.status,
        is_sendable=identity.is_sendable,
        dns_records=records,
        # PENDING_DNS is only ever set when we could not publish the records
        # ourselves, so it doubles as the "clinic must act" signal.
        dns_self_published=identity.status != EmailIdentityStatus.PENDING_DNS.value,
        verified_at=identity.verified_at.isoformat() if identity.verified_at else None,
        last_checked_at=(
            identity.last_checked_at.isoformat() if identity.last_checked_at else None
        ),
        failure_reason=identity.failure_reason,
    )


# ============================================================================
# Institution admin — read, edit display fields, re-check
# ============================================================================


@router.get("", response_model=EmailSendingIdentityListResponse)
@limiter.limit(RATE_READ)
async def list_email_sending_identities(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailSendingIdentityListResponse:
    institution_id = _require_institution(current_user)
    async with get_db_session() as session:
        identities = await EmailIdentityService(session).list_for_institution(
            institution_id
        )
        return EmailSendingIdentityListResponse(
            identities=[_to_response(i) for i in identities]
        )


@router.post("/{identity_id}/verify", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
async def recheck_email_sending_identity(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailSendingIdentityResponse:
    """Re-check verification now instead of waiting for the hourly sweep."""
    institution_id = _require_institution(current_user)
    async with get_db_session() as session:
        service = EmailIdentityService(session)
        identity = await _load_scoped(session, identity_id, institution_id)
        try:
            await service.refresh(identity)
        except SesProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return _to_response(identity)


@router.put("/{identity_id}", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
async def update_email_sending_identity(
    request: Request,
    identity_id: str,
    body: IdentityUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailSendingIdentityResponse:
    institution_id = _require_institution(current_user)
    async with get_db_session() as session:
        identity = await _load_scoped(session, identity_id, institution_id)
        if body.from_name is not None:
            identity.from_name = body.from_name.strip() or None
        if body.reply_to_address is not None:
            identity.reply_to_address = body.reply_to_address.strip() or None
        await session.flush()
        return _to_response(identity)


# ============================================================================
# Super admin — provision and remove
# ============================================================================


@router.post(
    "/provision",
    response_model=EmailSendingIdentityResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
async def provision_email_sending_identity(
    request: Request,
    body: ProvisionRequest,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> EmailSendingIdentityResponse:
    """Create the provider identity and record it.

    Safe to repeat: the provider returns the existing DKIM records for a domain
    that already exists, so a retry after a partial failure converges rather
    than erroring.
    """
    async with get_db_session() as session:
        try:
            identity = await EmailIdentityService(session).provision(
                institution_id=body.institution_id,
                location_id=body.location_id,
                from_name=body.from_name,
                reply_to_address=body.reply_to_address,
                local_part=body.local_part,
            )
        except SesProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _to_response(identity)


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
async def delete_email_sending_identity(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> None:
    """Remove the identity and everything provisioning created for it."""
    from src.app.models.email_sending_identity import EmailSendingIdentity

    async with get_db_session() as session:
        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found"
            )
        try:
            await EmailIdentityService(session).deprovision(identity)
        except SesProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc


async def _load_scoped(session, identity_id: str, institution_id: str):  # noqa: ANN001
    """Load an identity, refusing one that belongs to another institution.

    RLS already scopes the query, but an explicit check turns a cross-tenant id
    into a clean 404 rather than an empty result the caller has to interpret.
    """
    from src.app.models.email_sending_identity import EmailSendingIdentity

    identity = await session.get(EmailSendingIdentity, identity_id)
    if identity is None or str(identity.institution_id) != str(institution_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found"
        )
    return identity
