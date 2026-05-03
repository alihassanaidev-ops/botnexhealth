"""Admin API routes for institution management."""

from __future__ import annotations

import logging
from typing import Any, Annotated

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.api.deps import get_current_admin
from src.app.config import settings
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution import DEFAULT_JURISDICTION, Jurisdiction
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background
from src.app.services.audit_decorator import audit
from src.app.services.institution_service import InstitutionService
from src.app.services.user_invite_service import UserInviteService
from src.app.api.pagination import PaginationQuery, page_count, paginate
from src.app.api.models import (
    AuditLogPaginatedResponse,
    InstitutionBasicListResponse,
    InstitutionResponse,
)
from src.app.api.helpers import handle_nexhealth_request
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/institutions", tags=["Admin - Institutions"])


# =============================================================================
# Retell Agents API
# =============================================================================

@router.get("/retell/agents")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda *args, **kwargs: "retell:agents",
    actor=AuditActor.ADMIN,
)
async def list_retell_agents(
    _: User = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """
    List all Retell AI agents available for the configured Retell account.

    Used by Admins to select a Retell Agent when creating/configuring a Location.
    Uses the RETELL_API_SECRET from environment variables to authenticate.
    """
    from src.app.config import settings
    import httpx

    if not settings.retell_api_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retell API secret not configured"
        )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.retellai.com/list-agents",
                headers={"Authorization": f"Bearer {settings.retell_api_secret}"},
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch Retell agents: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to communicate with Retell API"
        )

@router.get("/retell/agents/{agent_id}")
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda *args, **kwargs: f"retell:agent:{kwargs.get('agent_id')}",
    actor=AuditActor.ADMIN,
)
async def verify_retell_agent(
    agent_id: str,
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    Verify a specific Retell AI agent exists by fetching its details.

    Used by Admins to verify a manually entered Retell Agent ID.
    """
    from src.app.config import settings
    import httpx

    if not settings.retell_api_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retell API secret not configured"
        )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.retellai.com/get-agent/{agent_id}",
                headers={"Authorization": f"Bearer {settings.retell_api_secret}"},
                timeout=10.0
            )
            if response.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found"
                )
            response.raise_for_status()
            return response.json()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch Retell agent {agent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to communicate with Retell API"
        )

# =============================================================================
# Request/Response Models
# =============================================================================

class InstitutionCreate(BaseModel):
    """Request body for creating an institution."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")

    # Initial Institution User (Mandatory)
    email: str = Field(..., description="Email for the initial institution user invite")

    # NexHealth
    nexhealth_api_key: str | None = None
    location_limit: int = Field(1, ge=1, le=500, description="Maximum number of locations this institution can have")

    # Regulatory jurisdiction (ISO 3166-2:CA)
    jurisdiction: Jurisdiction = Field(
        default=DEFAULT_JURISDICTION,
        description="Regulatory jurisdiction governing PHI for this institution",
    )


class InstitutionUpdate(BaseModel):
    """Request body for updating an institution.

    Uses exclude_unset so that omitted fields are ignored,
    while explicitly sending null clears the value.
    """
    name: str | None = None
    is_active: bool | None = None

    # NexHealth
    nexhealth_api_key: str | None = None
    location_limit: int | None = Field(None, ge=1, le=500)

    # Regulatory jurisdiction
    jurisdiction: Jurisdiction | None = None






# =============================================================================
# Routes
# =============================================================================

@router.get("/nexhealth/locations", response_model=InstitutionBasicListResponse)
@audit(
    AuditAction.READ_LOCATIONS,
    resource=lambda *args, **kwargs: "nexhealth:locations",
    actor=AuditActor.ADMIN,
)
async def list_nexhealth_locations(
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)],
    _: User = Depends(get_current_admin),
    subdomain: str | None = None,
) -> dict[str, Any]:
    """
    List all locations from the main NexHealth account.

    Used by Admins to select a location when creating/configuring an Institution.
    """
    params = {}
    if subdomain:
        params["subdomain"] = subdomain

    return await handle_nexhealth_request(client, "GET", "/locations", params=params)

@router.get("/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_all_audit_logs(
    _: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    institution_id: str | None = Query(None, description="Optional institution ID to filter logs")
):
    """
    Get audit logs across all institutions (Admin only).
    """
    from src.app.models.audit_log import AuditLog

    async with get_db_session() as session:
        data_stmt = select(AuditLog)

        # Apply institution filter if provided
        if institution_id:
            data_stmt = data_stmt.where(AuditLog.institution_id == institution_id)

        items, total = await paginate(
            PaginationQuery(session, data_stmt.order_by(AuditLog.timestamp.desc())),
            page=page,
            size=size,
        )

        return AuditLogPaginatedResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=page_count(total, size),
        )

@router.get("", response_model=list[InstitutionResponse])
async def list_institutions(
    include_inactive: bool = True,
    _: User = Depends(get_current_admin),
):
    """List all institutions with their primary user. Admins see soft-deleted institutions by default."""
    from src.app.models.institution import Institution
    from sqlalchemy.orm import selectinload

    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        institutions = await institution_service.list_all(include_inactive=include_inactive)

        # Single query to fetch one user per institution (avoids N+1)
        institution_ids = [t.id for t in institutions]
        if institution_ids:
            user_result = await session.execute(
                select(User)
                .where(
                    User.institution_id.in_(institution_ids),
                    User.role == UserRole.INSTITUTION_ADMIN.value,
                    User.deleted_at.is_(None),
                )
                .order_by(User.created_at.asc())
            )
            users_by_institution: dict[str, User] = {}
            for u in user_result.scalars().all():
                # Keep first user per institution (the primary institution user)
                if u.institution_id and u.institution_id not in users_by_institution:
                    users_by_institution[u.institution_id] = u

            from src.app.models.institution_location import InstitutionLocation
            retell_result = await session.execute(
                select(InstitutionLocation.institution_id)
                .where(InstitutionLocation.institution_id.in_(institution_ids))
                .where(InstitutionLocation.retell_agent_id.is_not(None))
                .where(InstitutionLocation.retell_agent_id != "")
                .distinct()
            )
            retell_institution_ids = set(retell_result.scalars().all())

        else:
            users_by_institution = {}
            retell_institution_ids = set()

        return [
            InstitutionResponse.from_institution(t, user=users_by_institution.get(t.id), has_retell_secret=(t.id in retell_institution_ids))
            for t in institutions
        ]


@router.post("", response_model=InstitutionResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.INSTITUTION_CREATE,
    resource=lambda request, data, _: f"slug:{data.slug}",
    actor=AuditActor.ADMIN
)
async def create_institution(
    request: Request,
    data: InstitutionCreate,
    _: User = Depends(get_current_admin),
):
    """Create a new institution with an initial institution user invite."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        invite_service = UserInviteService(session)
        email = UserInviteService.normalize_email(data.email)

        # --- Validate uniqueness BEFORE any mutations ---

        existing = await institution_service.get_by_slug(data.slug, include_inactive=True)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Institution with slug '{data.slug}' already exists"
            )

        existing_user = await session.execute(select(User).where(User.email == email))
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{email}' already exists"
            )

        # --- Create institution (flush only, not committed yet) ---

        institution_data = data.model_dump(exclude={"email"})
        try:
            institution = await institution_service.create(**institution_data)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Institution with slug '{data.slug}' already exists (race condition)"
            )

        # --- Create local user and send invite email ---
        try:
            user = await invite_service.create_invited_user(
                email=email,
                institution_id=str(institution.id),
                role=UserRole.INSTITUTION_ADMIN.value,
            )
        except Exception as e:
            logger.error("Initial institution invite failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )

        return InstitutionResponse.from_institution(institution, user, has_retell_secret=False)


@router.get("/{slug}", response_model=InstitutionResponse)
async def get_institution(
    slug: str,
    _: User = Depends(get_current_admin),
):
    """Get institution by slug (includes soft-deleted for admin visibility)."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        institution = await institution_service.get_by_slug(slug, include_inactive=True)

        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{slug}' not found"
            )

        # Fetch institution's primary user
        user_result = await session.execute(
            select(User)
            .where(
                User.institution_id == institution.id,
                User.role == UserRole.INSTITUTION_ADMIN.value,
                User.deleted_at.is_(None),
            )
            .order_by(User.created_at.asc())
            .limit(1)
        )
        institution_user = user_result.scalar_one_or_none()

        from src.app.models.institution_location import InstitutionLocation
        retell_result = await session.execute(
            select(InstitutionLocation.institution_id)
            .where(InstitutionLocation.institution_id == institution.id)
            .where(InstitutionLocation.retell_agent_id.is_not(None))
            .where(InstitutionLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None

        return InstitutionResponse.from_institution(institution, user=institution_user, has_retell_secret=has_retell)


@router.patch("/{slug}", response_model=InstitutionResponse)
@audit(
    AuditAction.INSTITUTION_UPDATE,
    resource=lambda request, slug, data, _: f"slug:{slug}",
    actor=AuditActor.ADMIN
)
async def update_institution(
    request: Request,
    slug: str,
    data: InstitutionUpdate,
    _: User = Depends(get_current_admin),
):
    """Update institution by slug."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        institution = await institution_service.get_by_slug(slug)

        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{slug}' not found"
            )

        # Only update fields that were explicitly sent in the request.
        # Using exclude_unset=True means omitted fields are ignored,
        # but explicitly sending null will clear the value (e.g., remove an API key).
        updates = data.model_dump(exclude_unset=True)
        institution = await institution_service.update(institution, **updates)

        from src.app.models.institution_location import InstitutionLocation
        retell_result = await session.execute(
            select(InstitutionLocation.institution_id)
            .where(InstitutionLocation.institution_id == institution.id)
            .where(InstitutionLocation.retell_agent_id.is_not(None))
            .where(InstitutionLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None

        return InstitutionResponse.from_institution(institution, has_retell_secret=has_retell)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
@audit(
    AuditAction.INSTITUTION_DELETE,
    resource=lambda request, slug, hard, _: f"slug:{slug}",
    actor=AuditActor.ADMIN
)
async def delete_institution(
    request: Request,
    slug: str,
    hard: bool = False,
    _: User = Depends(get_current_admin),
):
    """
    Delete institution by slug.

    Args:
        slug: Institution slug
        hard: If True, permanently delete. Default is soft delete.
    """
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        institution = await institution_service.get_by_slug(slug, include_inactive=True)

        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{slug}' not found"
            )

        await institution_service.delete(institution, hard_delete=hard)


class ResendInviteRequest(BaseModel):
    email: str = Field(..., description="Email of the user to re-invite")


class TestCallNotificationRequest(BaseModel):
    to_email: str = Field(..., description="Recipient email for the test notification")
    urgent: bool = Field(False, description="Send with URGENT emergency/complaint styling")
    tag: str | None = Field(
        default=None,
        description="Optional normalized call tag (example: appointment_booked, emergency, complaint)",
    )


@router.post("/{slug}/reinvite", status_code=status.HTTP_200_OK)
async def reinvite_institution_user(
    slug: str,
    data: ResendInviteRequest,
    current_admin: User = Depends(get_current_admin),
):
    """
    Re-invite an institution user with a fresh local invite token.
    """
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        invite_service = UserInviteService(session)
        email = UserInviteService.normalize_email(data.email)
        institution = await institution_service.get_by_slug(slug, include_inactive=True)

        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{slug}' not found"
            )

        # Find the local user
        result = await session.execute(
            select(User).where(
                User.email == email,
                User.institution_id == institution.id,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{email}' not found for institution '{slug}'"
            )

        try:
            await invite_service.reinvite_user(user)
        except Exception as e:
            logger.error("Institution reinvite failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to re-invite user"
            )

    log_audit_background(
        actor=current_admin.id,
        action=AuditAction.USER_REINVITED,
        target_resource=f"user:{email}:reinvite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_admin.role,
            "institution_id": str(institution.id),
            "location_id": str(user.location_id) if user.location_id else None,
            "old_user_id": str(user.id),
            "new_user_id": str(user.id),
            "role": user.role,
        },
        institution_id=str(institution.id),
        user_id=str(current_admin.id),
        location_id=str(user.location_id) if user.location_id else None,
    )
    return {"message": f"Invite re-sent to {email}"}


@router.post("/{slug}/test-call-notification", status_code=status.HTTP_202_ACCEPTED)
async def send_test_call_notification(
    slug: str,
    data: TestCallNotificationRequest,
    current_admin: User = Depends(get_current_admin),
):
    """
    Queue a synthetic call-notification email for testing from the Super Admin panel.

    This does not require any real Call records.
    """
    to_email = (data.to_email or "").strip().lower()
    if not to_email or "@" not in to_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid recipient email is required",
        )

    if not settings.celery_broker_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CELERY_BROKER_URL is not configured",
        )
    if not settings.resend_api_key or not settings.resend_from_email:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resend is not configured (RESEND_API_KEY / RESEND_FROM_EMAIL)",
        )

    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{slug}' not found",
            )

    from src.app.tasks.notifications import enqueue_test_call_notification

    enqueue_test_call_notification(
        recipients=[to_email],
        institution_slug=institution.slug,
        requested_by=current_admin.email,
        urgent=data.urgent,
        tag=data.tag,
    )

    return {
        "message": f"Test notification queued to {to_email}",
        "institution": institution.slug,
        "urgent": data.urgent,
    }


# =============================================================================
# Location Schemas
# =============================================================================

class LocationCreate(BaseModel):
    """Request body for creating a location."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")

    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None
    retell_agent_id: str | None = None
    twilio_from_number: str | None = None

    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    timezone: str | None = None


class LocationUpdate(BaseModel):
    """Request body for updating a location (PATCH)."""
    name: str | None = None
    is_active: bool | None = None

    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None
    retell_agent_id: str | None = None
    twilio_from_number: str | None = None

    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    timezone: str | None = None


class LocationUserResponse(BaseModel):
    """User assigned to a location."""
    id: str
    email: str
    role: str
    is_active: bool


class LocationResponse(BaseModel):
    """Response model for a location (no secrets)."""
    id: str
    institution_id: str
    name: str
    slug: str
    is_active: bool

    nexhealth_subdomain: str | None
    nexhealth_location_id: str | None
    retell_agent_id: str | None
    twilio_from_number: str | None

    address: str | None
    city: str | None
    state: str | None
    phone: str | None
    timezone: str | None

    user: LocationUserResponse | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_location(cls, loc: Any, user: Any = None) -> "LocationResponse":
        user_resp = None
        if user:
            user_resp = LocationUserResponse(
                id=str(user.id),
                email=user.email,
                role=user.role,
                is_active=user.is_active,
            )
        return cls(
            id=str(loc.id),
            institution_id=str(loc.institution_id),
            name=loc.name,
            slug=loc.slug,
            is_active=loc.is_active,
            nexhealth_subdomain=loc.nexhealth_subdomain,
            nexhealth_location_id=loc.nexhealth_location_id,
            retell_agent_id=loc.retell_agent_id,
            twilio_from_number=loc.twilio_from_number,
            address=loc.address,
            city=loc.city,
            state=loc.state,
            phone=loc.phone,
            timezone=loc.timezone,
            user=user_resp,
        )


# =============================================================================
# Location Routes
# =============================================================================

@router.post("/{slug}/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.LOCATION_CREATE,
    resource=lambda request, slug, data, _: f"institution:{slug}/location:{data.slug}",
    actor=AuditActor.ADMIN,
)
async def create_location(
    request: Request,
    slug: str,
    data: LocationCreate,
    _: User = Depends(get_current_admin),
):
    """Create a new location under an institution."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        existing = await institution_service.get_location_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Location with slug '{data.slug}' already exists",
            )

        location_data = data.model_dump()
        try:
            location = await institution_service.create_location(institution.id, **location_data)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Location with slug '{data.slug}' already exists (race condition)",
            )

        return LocationResponse.from_location(location)


@router.get("/{slug}/locations", response_model=list[LocationResponse])
async def list_locations(
    slug: str,
    include_inactive: bool = False,
    _: User = Depends(get_current_admin),
):
    """List all locations for an institution, including assigned location users."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        locations = await institution_service.list_locations(institution.id, include_inactive=include_inactive)

        # Fetch location users in one query (avoids N+1)
        location_ids = [loc.id for loc in locations]
        users_by_location: dict[str, User] = {}
        if location_ids:
            user_result = await session.execute(
                select(User).where(
                    User.location_id.in_(location_ids),
                    User.role == UserRole.LOCATION_ADMIN.value,
                    User.deleted_at.is_(None),
                )
            )
            for u in user_result.scalars().all():
                if u.location_id and u.location_id not in users_by_location:
                    users_by_location[u.location_id] = u

        return [
            LocationResponse.from_location(loc, user=users_by_location.get(loc.id))
            for loc in locations
        ]


@router.get("/{slug}/locations/{loc_slug}", response_model=LocationResponse)
async def get_location(
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """Get a specific location by slug."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        return LocationResponse.from_location(location)


@router.patch("/{slug}/locations/{loc_slug}", response_model=LocationResponse)
@audit(
    AuditAction.LOCATION_UPDATE,
    resource=lambda request, slug, loc_slug, data, _: f"institution:{slug}/location:{loc_slug}",
    actor=AuditActor.ADMIN,
)
async def update_location(
    request: Request,
    slug: str,
    loc_slug: str,
    data: LocationUpdate,
    _: User = Depends(get_current_admin),
):
    """Update a location by slug."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        updates = data.model_dump(exclude_unset=True)
        location = await institution_service.update_location(location, **updates)
        return LocationResponse.from_location(location)


@router.delete("/{slug}/locations/{loc_slug}", status_code=status.HTTP_204_NO_CONTENT)
@audit(
    AuditAction.LOCATION_DELETE,
    resource=lambda request, slug, loc_slug, hard, _: f"institution:{slug}/location:{loc_slug}",
    actor=AuditActor.ADMIN,
)
async def delete_location(
    request: Request,
    slug: str,
    loc_slug: str,
    hard: bool = False,
    _: User = Depends(get_current_admin),
):
    """Delete a location (soft or hard)."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        await institution_service.delete_location(location, hard=hard)


@router.post("/{slug}/locations/{loc_slug}/sync")
@audit(
    AuditAction.LOCATION_SYNC,
    resource=lambda request, slug, loc_slug, _: f"institution:{slug}/location:{loc_slug}",
    actor=AuditActor.ADMIN,
)
async def sync_location(
    request: Request,
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """Trigger a PMS sync for a specific location."""
    from src.app.services.sync_service import SyncService

    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        sync_service = SyncService(session)
        result = await sync_service.sync_location(institution, location)

        return {
            "location": loc_slug,
            "success": result.success,
            "providers_synced": result.providers_synced,
            "appointment_types_synced": result.appointment_types_synced,
            "errors": result.errors,
        }


# =============================================================================
# Location User Management
# =============================================================================

class LocationUserInvite(BaseModel):
    """Request body for inviting a location user."""
    email: str = Field(..., description="Email for the location user invite")


@router.post("/{slug}/locations/{loc_slug}/invite", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.LOCATION_USER_CREATE,
    resource=lambda request, slug, loc_slug, data, _: f"institution:{slug}/location:{loc_slug}/user:{data.email}",
    actor=AuditActor.ADMIN,
)
async def invite_location_user(
    request: Request,
    slug: str,
    loc_slug: str,
    data: LocationUserInvite,
    _: User = Depends(get_current_admin),
):
    """
    Invite a user with LOCATION_ADMIN role scoped to a specific location.

    Flow mirrors create_institution:
    1. Validate institution + location exist
    2. Check email uniqueness
    3. Create local invite state
    4. Send invite email
    """
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        invite_service = UserInviteService(session)
        email = UserInviteService.normalize_email(data.email)

        # Validate institution
        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        # Validate location
        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        # Check email uniqueness
        existing_user = await session.execute(
            select(User).where(User.email == email)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{email}' already exists"
            )

        # Check if location already has a user
        existing_loc_user = await session.execute(
            select(User).where(
                User.location_id == location.id,
                User.role == UserRole.LOCATION_ADMIN.value,
                User.deleted_at.is_(None),
            )
        )
        if existing_loc_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Location '{loc_slug}' already has an assigned user"
            )

        # Create local user and send invite email
        try:
            user = await invite_service.create_invited_user(
                email=email,
                institution_id=str(institution.id),
                role=UserRole.LOCATION_ADMIN.value,
                location_id=str(location.id),
            )
        except Exception as e:
            logger.error("Location user invite failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invite email",
            )

        return LocationResponse.from_location(location, user=user)


@router.get("/{slug}/locations/{loc_slug}/users", response_model=list[LocationUserResponse])
async def list_location_users(
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """List all users assigned to a specific location."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        result = await session.execute(
            select(User).where(
                User.location_id == location.id,
                User.role.in_([UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value]),
                User.deleted_at.is_(None),
            )
        )
        users = result.scalars().all()

        return [
            LocationUserResponse(
                id=str(u.id),
                email=u.email,
                role=u.role,
                is_active=u.is_active,
            )
            for u in users
        ]


@router.post("/{slug}/locations/{loc_slug}/reinvite", status_code=status.HTTP_200_OK)
async def reinvite_location_user(
    slug: str,
    loc_slug: str,
    data: ResendInviteRequest,
    current_admin: User = Depends(get_current_admin),
):
    """
    Re-invite a location user with a fresh local invite token.
    """
    async with get_db_session() as session:
        institution_service = InstitutionService(session)
        invite_service = UserInviteService(session)
        email = UserInviteService.normalize_email(data.email)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        # Find the user
        result = await session.execute(
            select(User).where(
                User.email == email,
                User.location_id == location.id,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{email}' not found for location '{loc_slug}'",
            )

        try:
            await invite_service.reinvite_user(user)
        except Exception as e:
            logger.error("Location reinvite failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to re-invite user",
            )

    log_audit_background(
        actor=current_admin.id,
        action=AuditAction.USER_REINVITED,
        target_resource=f"location:{loc_slug}/user:{email}:reinvite",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_admin.role,
            "institution_id": str(institution.id),
            "location_id": str(location.id),
            "old_user_id": str(user.id),
            "new_user_id": str(user.id),
            "role": user.role,
        },
        institution_id=str(institution.id),
        user_id=str(current_admin.id),
        location_id=str(location.id),
    )
    return {"message": f"Invite re-sent to {email}"}


# =============================================================================
# Operating Hours & Breaks Schemas
# =============================================================================

class OperatingHoursEntry(BaseModel):
    """One day's operating hours."""
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday … 6=Sunday")
    is_open: bool = True
    open_time: str | None = Field(None, description="HH:MM format, e.g. '08:00'")
    close_time: str | None = Field(None, description="HH:MM format, e.g. '17:00'")


class OperatingHoursResponse(BaseModel):
    id: str
    location_id: str
    day_of_week: int
    is_open: bool
    open_time: str | None = None
    close_time: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, m: Any) -> "OperatingHoursResponse":
        return cls(
            id=str(m.id),
            location_id=str(m.location_id),
            day_of_week=m.day_of_week,
            is_open=m.is_open,
            open_time=m.open_time.strftime("%H:%M") if m.open_time else None,
            close_time=m.close_time.strftime("%H:%M") if m.close_time else None,
        )


class BulkOperatingHoursRequest(BaseModel):
    """Bulk-set all 7 days at once."""
    hours: list[OperatingHoursEntry] = Field(..., min_length=1, max_length=7)


class BreakCreateRequest(BaseModel):
    """Create a new break for a location."""
    name: str = Field(..., min_length=1, max_length=100)
    day_of_week: int | None = Field(None, ge=0, le=6, description="NULL = every day")
    start_time: str = Field(..., description="HH:MM format")
    end_time: str = Field(..., description="HH:MM format")


class BreakResponse(BaseModel):
    id: str
    location_id: str
    name: str
    day_of_week: int | None = None
    start_time: str
    end_time: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, m: Any) -> "BreakResponse":
        return cls(
            id=str(m.id),
            location_id=str(m.location_id),
            name=m.name,
            day_of_week=m.day_of_week,
            start_time=m.start_time.strftime("%H:%M"),
            end_time=m.end_time.strftime("%H:%M"),
        )


# =============================================================================
# Operating Hours Routes
# =============================================================================

@router.get("/{slug}/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse])
async def get_operating_hours(
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """Get operating hours for a location."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        from src.app.models.location_operating_hours import LocationOperatingHours
        result = await session.execute(
            select(LocationOperatingHours)
            .where(LocationOperatingHours.location_id == location.id)
            .order_by(LocationOperatingHours.day_of_week)
        )
        return [OperatingHoursResponse.from_model(h) for h in result.scalars().all()]


@router.put("/{slug}/locations/{loc_slug}/operating-hours", response_model=list[OperatingHoursResponse])
async def set_operating_hours(
    slug: str,
    loc_slug: str,
    data: BulkOperatingHoursRequest,
    _: User = Depends(get_current_admin),
):
    """Bulk-set operating hours for a location (replaces existing)."""
    from datetime import time as dt_time
    from src.app.models.location_operating_hours import LocationOperatingHours

    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        # Validate no duplicate days
        days_seen = set()
        for entry in data.hours:
            if entry.day_of_week in days_seen:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Duplicate day_of_week: {entry.day_of_week}",
                )
            days_seen.add(entry.day_of_week)

        # Delete existing hours for this location
        from sqlalchemy import delete
        await session.execute(
            delete(LocationOperatingHours).where(
                LocationOperatingHours.location_id == location.id
            )
        )

        # Insert new hours
        new_rows = []
        for entry in data.hours:
            open_t = dt_time.fromisoformat(entry.open_time) if entry.open_time else None
            close_t = dt_time.fromisoformat(entry.close_time) if entry.close_time else None
            row = LocationOperatingHours(
                location_id=location.id,
                day_of_week=entry.day_of_week,
                is_open=entry.is_open,
                open_time=open_t,
                close_time=close_t,
            )
            session.add(row)
            new_rows.append(row)

        await session.flush()
        return [OperatingHoursResponse.from_model(r) for r in new_rows]


# =============================================================================
# Breaks Routes
# =============================================================================

@router.get("/{slug}/locations/{loc_slug}/breaks", response_model=list[BreakResponse])
async def get_breaks(
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """Get breaks for a location."""
    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        from src.app.models.location_break import LocationBreak
        result = await session.execute(
            select(LocationBreak)
            .where(LocationBreak.location_id == location.id)
            .order_by(LocationBreak.day_of_week.nulls_first(), LocationBreak.start_time)
        )
        return [BreakResponse.from_model(b) for b in result.scalars().all()]


@router.post(
    "/{slug}/locations/{loc_slug}/breaks",
    response_model=BreakResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_break(
    slug: str,
    loc_slug: str,
    data: BreakCreateRequest,
    _: User = Depends(get_current_admin),
):
    """Add a break to a location."""
    from datetime import time as dt_time
    from src.app.models.location_break import LocationBreak

    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        brk = LocationBreak(
            location_id=location.id,
            name=data.name,
            day_of_week=data.day_of_week,
            start_time=dt_time.fromisoformat(data.start_time),
            end_time=dt_time.fromisoformat(data.end_time),
        )
        session.add(brk)
        await session.flush()
        return BreakResponse.from_model(brk)


@router.delete(
    "/{slug}/locations/{loc_slug}/breaks/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_break(
    slug: str,
    loc_slug: str,
    break_id: str,
    _: User = Depends(get_current_admin),
):
    """Remove a break from a location."""
    from src.app.models.location_break import LocationBreak

    async with get_db_session() as session:
        institution_service = InstitutionService(session)

        institution = await institution_service.get_by_slug(slug, include_inactive=True)
        if not institution:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Institution '{slug}' not found")

        location = await institution_service.get_location_by_slug(loc_slug)
        if not location or location.institution_id != institution.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        result = await session.execute(
            select(LocationBreak).where(
                LocationBreak.id == break_id,
                LocationBreak.location_id == location.id,
            )
        )
        brk = result.scalar_one_or_none()
        if not brk:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Break not found")

        await session.delete(brk)
