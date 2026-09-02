"""Routes for a clinic's email sending identity.

Split by who should be able to do what:

* **Institution admins** see their domains, manage multiple sender addresses
  and institution/location defaults, and re-check verification. A
  **super admin** reaches the same surface for any institution by naming it in
  ``institution_id``.
* **Location admins** may read available receiving domains for their own inbox
  settings but cannot create or reassign senders.
* **Super admins** register, activate/deactivate, and remove domains. Provisioning
  creates real AWS resources — an SES identity, a tenant, a configuration set,
  and DNS records — against capped quotas. Verification never activates live
  delivery by itself.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from src.app.api.deps import (
    get_current_institution_location_or_super_admin,
    get_current_institution_or_super_admin,
    get_current_super_admin,
    resolve_target_institution,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.config import settings
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.email_sending_identity import EmailIdentityStatus
from src.app.models.email_sender_address import EmailSenderAddress
from src.app.models.user import User
from src.app.services.audit_decorator import audit
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
    purpose: str = "sending"


class EmailSenderAddressResponse(BaseModel):
    id: str
    email_identity_id: str
    institution_id: str
    location_id: str | None
    local_part: str
    from_address: str
    from_name: str | None
    external_reply_to: str | None
    is_active: bool
    is_default: bool


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
    #: Explicit operator rollout state. Verification alone never enables sends.
    is_active: bool
    #: True only when the domain is verified and safe to send from.
    is_sendable: bool
    can_activate: bool
    activation_blocker: str | None
    dns_records: list[DnsRecordResponse]
    #: True when we published the records ourselves, so the clinic has nothing
    #: to do. False means the records below must be published manually.
    dns_self_published: bool
    verified_at: str | None
    last_checked_at: str | None
    failure_reason: str | None
    inbound_domain: str | None
    inbound_enabled: bool
    inbound_dns_records: list[DnsRecordResponse]
    addresses: list[EmailSenderAddressResponse]


class EmailSendingIdentityListResponse(BaseModel):
    identities: list[EmailSendingIdentityResponse]


class ProvisionRequest(BaseModel):
    institution_id: str
    #: Omit for the institution-wide identity.
    location_id: str | None = None
    from_name: str | None = Field(default=None, max_length=255)
    reply_to_address: str | None = Field(default=None, max_length=320)
    local_part: str = Field(default="hello", max_length=64, pattern=r"^[a-z0-9._-]+$")
    #: A clinic-owned domain. Omit only when deliberately using a generated
    #: ScaleNexus fallback subdomain.
    domain: str | None = Field(default=None, max_length=253)
    #: Dedicated receiving subdomain, normally ``reply.<domain>``. It must not
    #: be the clinic's root domain because changing that MX would steal its mail.
    inbound_domain: str | None = Field(default=None, max_length=253)

    @field_validator("reply_to_address")
    @classmethod
    def valid_reply_to(cls, value: str | None) -> str | None:
        return _validated_email(value)


class IdentityUpdateRequest(BaseModel):
    """Display fields only. The domain and address are immutable — changing them
    would mean re-verifying, which is a provisioning operation."""

    from_name: str | None = Field(default=None, max_length=255)
    reply_to_address: str | None = Field(default=None, max_length=320)

    @field_validator("reply_to_address")
    @classmethod
    def valid_reply_to(cls, value: str | None) -> str | None:
        return _validated_email(value)


class SenderAddressRequest(BaseModel):
    location_id: str | None = None
    local_part: str = Field(max_length=64, pattern=r"^[a-z0-9._-]+$")
    from_name: str | None = Field(default=None, max_length=255)
    external_reply_to: str | None = Field(default=None, max_length=320)
    make_default: bool | None = None

    @field_validator("external_reply_to")
    @classmethod
    def valid_external_reply_to(cls, value: str | None) -> str | None:
        return _validated_email(value)


class SenderAddressUpdateRequest(BaseModel):
    from_name: str | None = Field(default=None, max_length=255)
    external_reply_to: str | None = Field(default=None, max_length=320)
    is_active: bool | None = None

    @field_validator("external_reply_to")
    @classmethod
    def valid_external_reply_to(cls, value: str | None) -> str | None:
        return _validated_email(value)


def _validated_email(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    _display, parsed = parseaddr(cleaned)
    if (
        parsed != cleaned
        or "@" not in parsed
        or parsed.startswith("@")
        or parsed.endswith("@")
    ):
        raise ValueError("Enter a valid reply-to email address")
    return cleaned


def _address_response(address: EmailSenderAddress) -> EmailSenderAddressResponse:
    return EmailSenderAddressResponse(
        id=str(address.id),
        email_identity_id=str(address.email_identity_id),
        institution_id=str(address.institution_id),
        location_id=str(address.location_id) if address.location_id else None,
        local_part=address.local_part,
        from_address=address.from_address,
        from_name=address.from_name,
        external_reply_to=address.external_reply_to,
        is_active=address.is_active,
        is_default=address.is_default,
    )


def _to_response(
    identity: Any, addresses: list[EmailSenderAddress] | None = None
) -> EmailSendingIdentityResponse:
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
        is_active=identity.is_active,
        is_sendable=(
            identity.is_sendable
            and (identity.provider != "ses" or settings.ses_clinic_sending_enabled)
        ),
        can_activate=(
            identity.status == EmailIdentityStatus.VERIFIED.value
            and settings.ses_clinic_sending_enabled
        ),
        activation_blocker=_activation_blocker(identity),
        dns_records=records,
        # PENDING_DNS is only ever set when we could not publish the records
        # ourselves, so it doubles as the "clinic must act" signal.
        dns_self_published=bool(identity.dns_managed),
        verified_at=identity.verified_at.isoformat() if identity.verified_at else None,
        last_checked_at=(
            identity.last_checked_at.isoformat() if identity.last_checked_at else None
        ),
        failure_reason=identity.failure_reason,
        inbound_domain=identity.inbound_domain,
        inbound_enabled=identity.inbound_enabled,
        inbound_dns_records=[
            DnsRecordResponse(**r) for r in (identity.inbound_dns_records or [])
        ],
        addresses=[_address_response(a) for a in (addresses or [])],
    )


def _activation_blocker(identity: Any) -> str | None:
    if identity.status != EmailIdentityStatus.VERIFIED.value:
        return "Verify the sending domain before activating it."
    if not settings.ses_clinic_sending_enabled:
        return (
            "SES activation is locked until platform delivery events, suppression, "
            "and monitoring are enabled."
        )
    return None


# ============================================================================
# Institution admin — read, edit display fields, re-check
# ============================================================================


@router.get("", response_model=EmailSendingIdentityListResponse)
@limiter.limit(RATE_READ)
async def list_email_sending_identities(
    request: Request,
    current_user: Annotated[
        User, Depends(get_current_institution_location_or_super_admin)
    ],
    institution_id: str | None = None,
) -> EmailSendingIdentityListResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        identities = await EmailIdentityService(session).list_for_institution(
            institution_id
        )
        addresses = await EmailIdentityService(session).list_addresses(institution_id)
        return EmailSendingIdentityListResponse(
            identities=[
                _to_response(
                    i,
                    [a for a in addresses if str(a.email_identity_id) == str(i.id)],
                )
                for i in identities
            ]
        )


@router.post("/{identity_id}/verify", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
async def recheck_email_sending_identity(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> EmailSendingIdentityResponse:
    """Re-check verification now instead of waiting for the hourly sweep."""
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        service = EmailIdentityService(session)
        identity = await _load_scoped(session, identity_id, institution_id)
        try:
            await service.refresh(identity)
        except SesProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.put("/{identity_id}", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_UPDATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}",
    actor=AuditActor.ADMIN,
)
async def update_email_sending_identity(
    request: Request,
    identity_id: str,
    body: IdentityUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> EmailSendingIdentityResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    request.state.audit_institution_id = institution_id
    async with get_db_session() as session:
        identity = await _load_scoped(session, identity_id, institution_id)
        addresses = await _addresses_for_domain(session, identity.id)
        legacy_address = next(
            (a for a in addresses if a.from_address.lower() == identity.from_address.lower()),
            None,
        )
        if body.from_name is not None:
            identity.from_name = body.from_name.strip() or None
            if legacy_address is not None:
                legacy_address.from_name = identity.from_name
        if body.reply_to_address is not None:
            identity.reply_to_address = body.reply_to_address.strip() or None
            if legacy_address is not None:
                legacy_address.external_reply_to = identity.reply_to_address
        await session.flush()
        return _to_response(identity, addresses)


# ============================================================================
# Super admin — provision and remove
# ============================================================================


@router.post(
    "/provision",
    response_model=EmailSendingIdentityResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_PROVISION,
    resource=lambda request, body, **_: (
        f"institution:{body.institution_id}:email_identity:"
        f"{body.location_id or 'default'}"
    ),
    actor=AuditActor.ADMIN,
)
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
    request.state.audit_institution_id = body.institution_id
    request.state.audit_location_id = body.location_id
    async with get_db_session() as session:
        try:
            identity = await EmailIdentityService(session).provision(
                institution_id=body.institution_id,
                location_id=body.location_id,
                from_name=body.from_name,
                reply_to_address=body.reply_to_address,
                local_part=body.local_part,
                domain=body.domain,
                inbound_domain=body.inbound_domain,
            )
        except SesProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.post("/{identity_id}/activate", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_ACTIVATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}",
    actor=AuditActor.ADMIN,
)
async def activate_email_sending_identity(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> EmailSendingIdentityResponse:
    """Route this scope through SES after verification and platform sign-off."""
    async with get_db_session() as session:
        from src.app.models.email_sending_identity import EmailSendingIdentity

        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found"
            )
        request.state.audit_institution_id = str(identity.institution_id)
        request.state.audit_location_id = (
            str(identity.location_id) if identity.location_id else None
        )
        blocker = _activation_blocker(identity)
        if blocker:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocker)
        identity.is_active = True
        await session.flush()
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.post("/{identity_id}/deactivate", response_model=EmailSendingIdentityResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_DEACTIVATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}",
    actor=AuditActor.ADMIN,
)
async def deactivate_email_sending_identity(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> EmailSendingIdentityResponse:
    """Immediately fall back to the platform sender without removing DNS."""
    async with get_db_session() as session:
        from src.app.models.email_sending_identity import EmailSendingIdentity

        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found"
            )
        request.state.audit_institution_id = str(identity.institution_id)
        request.state.audit_location_id = (
            str(identity.location_id) if identity.location_id else None
        )
        identity.is_active = False
        await session.flush()
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.post(
    "/{identity_id}/activate-inbound",
    response_model=EmailSendingIdentityResponse,
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_INBOUND_ACTIVATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}:inbound",
    actor=AuditActor.ADMIN,
)
async def activate_inbound_domain(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> EmailSendingIdentityResponse:
    """Enable a clinic-owned receiving subdomain after its DNS is verified."""
    from src.app.models.email_sending_identity import EmailSendingIdentity

    async with get_db_session() as session:
        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(status_code=404, detail="Identity not found")
        if not identity.inbound_domain:
            raise HTTPException(status_code=409, detail="No receiving domain is configured")
        try:
            receiving_ready, detail = await EmailIdentityService(
                session
            ).provisioning.check_receiving_status(identity.inbound_domain)
        except SesProvisioningError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not receiving_ready:
            raise HTTPException(
                status_code=409,
                detail=detail or "Receiving domain DNS is not verified yet",
            )
        identity.inbound_enabled = True
        request.state.audit_institution_id = str(identity.institution_id)
        await session.flush()
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.post(
    "/{identity_id}/deactivate-inbound",
    response_model=EmailSendingIdentityResponse,
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_INBOUND_DEACTIVATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}:inbound",
    actor=AuditActor.ADMIN,
)
async def deactivate_inbound_domain(
    request: Request,
    identity_id: str,
    current_user: Annotated[User, Depends(get_current_super_admin)],
) -> EmailSendingIdentityResponse:
    from src.app.models.email_sending_identity import EmailSendingIdentity

    async with get_db_session() as session:
        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(status_code=404, detail="Identity not found")
        identity.inbound_enabled = False
        request.state.audit_institution_id = str(identity.institution_id)
        await session.flush()
        return _to_response(identity, await _addresses_for_domain(session, identity.id))


@router.post(
    "/{identity_id}/addresses",
    response_model=EmailSenderAddressResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_SENDER_ADDRESS_CREATE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}:address",
    actor=AuditActor.ADMIN,
)
async def create_sender_address(
    request: Request,
    identity_id: str,
    body: SenderAddressRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
) -> EmailSenderAddressResponse:
    from src.app.models.email_sending_identity import EmailSendingIdentity

    async with get_db_session() as session:
        identity = await session.get(EmailSendingIdentity, identity_id)
        if identity is None:
            raise HTTPException(status_code=404, detail="Domain not found")
        institution_id = resolve_target_institution(
            current_user, str(identity.institution_id)
        )
        location_id = body.location_id
        try:
            address = await EmailIdentityService(session).create_address(
                identity,
                institution_id=institution_id,
                location_id=location_id,
                local_part=body.local_part,
                from_name=body.from_name,
                external_reply_to=body.external_reply_to,
                make_default=body.make_default,
            )
        except SesProvisioningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        request.state.audit_institution_id = institution_id
        request.state.audit_location_id = location_id
        return _address_response(address)


@router.put(
    "/addresses/{address_id}", response_model=EmailSenderAddressResponse
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_SENDER_ADDRESS_UPDATE,
    resource=lambda request, address_id, **_: f"email_sender_address:{address_id}",
    actor=AuditActor.ADMIN,
)
async def update_sender_address(
    request: Request,
    address_id: str,
    body: SenderAddressUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
) -> EmailSenderAddressResponse:
    async with get_db_session() as session:
        address = await _load_address_for_user(session, address_id, current_user)
        if body.is_active is False and address.is_default:
            raise HTTPException(
                status_code=409,
                detail="Choose another default before disabling this address",
            )
        if body.from_name is not None:
            address.from_name = body.from_name.strip() or None
        if body.external_reply_to is not None:
            address.external_reply_to = body.external_reply_to.strip() or None
        if body.is_active is not None:
            address.is_active = body.is_active
        request.state.audit_institution_id = str(address.institution_id)
        request.state.audit_location_id = (
            str(address.location_id) if address.location_id else None
        )
        await session.flush()
        return _address_response(address)


@router.post(
    "/addresses/{address_id}/default", response_model=EmailSenderAddressResponse
)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_SENDER_ADDRESS_DEFAULT,
    resource=lambda request, address_id, **_: f"email_sender_address:{address_id}:default",
    actor=AuditActor.ADMIN,
)
async def make_sender_address_default(
    request: Request,
    address_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
) -> EmailSenderAddressResponse:
    async with get_db_session() as session:
        address = await _load_address_for_user(session, address_id, current_user)
        await EmailIdentityService(session).set_default(address)
        request.state.audit_institution_id = str(address.institution_id)
        request.state.audit_location_id = (
            str(address.location_id) if address.location_id else None
        )
        return _address_response(address)


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_SENDER_ADDRESS_DELETE,
    resource=lambda request, address_id, **_: f"email_sender_address:{address_id}",
    actor=AuditActor.ADMIN,
)
async def delete_sender_address(
    request: Request,
    address_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
) -> None:
    async with get_db_session() as session:
        address = await _load_address_for_user(session, address_id, current_user)
        if address.is_default:
            raise HTTPException(
                status_code=409,
                detail="Choose another default before removing this address",
            )
        request.state.audit_institution_id = str(address.institution_id)
        request.state.audit_location_id = (
            str(address.location_id) if address.location_id else None
        )
        await session.delete(address)
        await session.flush()


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_IDENTITY_DELETE,
    resource=lambda request, identity_id, **_: f"email_identity:{identity_id}",
    actor=AuditActor.ADMIN,
)
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
        if identity.is_active or identity.inbound_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Deactivate sending and receiving before removing this domain",
            )
        request.state.audit_institution_id = str(identity.institution_id)
        request.state.audit_location_id = (
            str(identity.location_id) if identity.location_id else None
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


async def _addresses_for_domain(session, identity_id: str) -> list[EmailSenderAddress]:  # noqa: ANN001
    result = await session.execute(
        select(EmailSenderAddress)
        .where(EmailSenderAddress.email_identity_id == identity_id)
        .order_by(
            EmailSenderAddress.location_id.nulls_first(),
            EmailSenderAddress.is_default.desc(),
            EmailSenderAddress.from_address,
        )
    )
    return list(result.scalars().all())


async def _load_address_for_user(
    session, address_id: str, user: User  # noqa: ANN001
) -> EmailSenderAddress:
    address = await session.get(EmailSenderAddress, address_id)
    if address is None:
        raise HTTPException(status_code=404, detail="Sender address not found")
    resolve_target_institution(user, str(address.institution_id))
    return address
