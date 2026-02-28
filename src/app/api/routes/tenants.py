"""Admin API routes for tenant management."""

from __future__ import annotations

import logging
from typing import Any, Annotated

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.api.deps import get_current_admin
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.tenant_service import TenantService
from src.app.services.supabase_service import SupabaseService
from src.app.services.supabase_service import SupabaseService
from src.app.api.models import TenantResponse, InstitutionBasicListResponse, AuditLogPaginatedResponse
from src.app.api.helpers import handle_nexhealth_request
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tenants", tags=["Admin - Tenants"])


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

class TenantCreate(BaseModel):
    """Request body for creating a tenant."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")

    # Initial Tenant User (Mandatory)
    email: str = Field(..., description="Email for the initial tenant user (Supabase invite)")

    # NexHealth
    nexhealth_api_key: str | None = None


class TenantUpdate(BaseModel):
    """Request body for updating a tenant.

    Uses exclude_unset so that omitted fields are ignored,
    while explicitly sending null clears the value.
    """
    name: str | None = None
    is_active: bool | None = None

    # NexHealth
    nexhealth_api_key: str | None = None






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
    
    Used by Admins to select a location when creating/configuring a Tenant.
    """
    params = {}
    if subdomain:
        params["subdomain"] = subdomain
        
    return await handle_nexhealth_request(client, "GET", "/locations", params=params)

@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    include_inactive: bool = True,
    _: User = Depends(get_current_admin),
):
    """List all tenants with their primary user. Admins see soft-deleted tenants by default."""
    from src.app.models.tenant import Tenant
    from sqlalchemy.orm import selectinload

    async with get_db_session() as session:
        service = TenantService(session)
        tenants = await service.list_all(include_inactive=include_inactive)

        # Single query to fetch one user per tenant (avoids N+1)
        tenant_ids = [t.id for t in tenants]
        if tenant_ids:
            user_result = await session.execute(
                select(User).where(User.tenant_id.in_(tenant_ids))
            )
            users_by_tenant: dict[str, User] = {}
            for u in user_result.scalars().all():
                # Keep first user per tenant (the primary tenant user)
                if u.tenant_id and u.tenant_id not in users_by_tenant:
                    users_by_tenant[u.tenant_id] = u
                    
            from src.app.models.tenant_location import TenantLocation
            retell_result = await session.execute(
                select(TenantLocation.tenant_id)
                .where(TenantLocation.tenant_id.in_(tenant_ids))
                .where(TenantLocation.retell_agent_id.is_not(None))
                .where(TenantLocation.retell_agent_id != "")
                .distinct()
            )
            retell_tenant_ids = set(retell_result.scalars().all())

        else:
            users_by_tenant = {}
            retell_tenant_ids = set()

        return [
            TenantResponse.from_tenant(t, user=users_by_tenant.get(t.id), has_retell_secret=(t.id in retell_tenant_ids))
            for t in tenants
        ]


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.TENANT_CREATE,
    resource=lambda request, data, _: f"slug:{data.slug}",
    actor=AuditActor.ADMIN
)
async def create_tenant(
    request: Request,
    data: TenantCreate,
    _: User = Depends(get_current_admin),
):
    """Create a new tenant with an initial tenant user via Supabase invite."""
    async with get_db_session() as session:
        service = TenantService(session)
        supabase_service = SupabaseService()

        # --- Validate uniqueness BEFORE any mutations ---

        existing = await service.get_by_slug(data.slug, include_inactive=True)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with slug '{data.slug}' already exists"
            )

        existing_user = await session.execute(
            select(User).where(User.email == data.email)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{data.email}' already exists"
            )

        # --- Create tenant (flush only, not committed yet) ---

        tenant_data = data.model_dump(exclude={"email"})
        try:
            tenant = await service.create(**tenant_data)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with slug '{data.slug}' already exists (race condition)"
            )

        # --- Invite user via Supabase ---

        supabase_user_id = None
        try:
            response = supabase_service.invite_user(
                email=data.email,
                tenant_id=str(tenant.id),
                role=UserRole.TENANT.value
            )
            if hasattr(response, 'user') and hasattr(response.user, 'id'):
                supabase_user_id = str(response.user.id)
            elif isinstance(response, dict) and 'id' in response:
                supabase_user_id = str(response['id'])
        except Exception as e:
            logger.error(f"Supabase invite failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send Supabase invite"
            )

        if not supabase_user_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Supabase invite did not return a user ID"
            )

        # --- Create local user record (id = Supabase UUID) ---

        user = User(
            id=supabase_user_id,
            email=data.email,
            role=UserRole.TENANT.value,
            tenant_id=tenant.id,
            is_active=True
        )
        session.add(user)

        try:
            await session.commit()
        except Exception as e:
            logger.error(f"Failed to commit tenant creation to DB: {e}")
            # Compensating transaction: clean up the orphaned Supabase user
            if supabase_user_id:
                logger.warning(f"Compensating: deleting Supabase user {supabase_user_id}")
                supabase_service.delete_user(supabase_user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create tenant locally. External invite rolled back."
            )

        return TenantResponse.from_tenant(tenant, user, has_retell_secret=False)


@router.get("/{slug}", response_model=TenantResponse)
async def get_tenant(
    slug: str,
    _: User = Depends(get_current_admin),
):
    """Get tenant by slug (includes soft-deleted for admin visibility)."""
    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_slug(slug, include_inactive=True)

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{slug}' not found"
            )

        # Fetch tenant's primary user
        user_result = await session.execute(
            select(User).where(User.tenant_id == tenant.id).limit(1)
        )
        tenant_user = user_result.scalar_one_or_none()
        
        from src.app.models.tenant_location import TenantLocation
        retell_result = await session.execute(
            select(TenantLocation.tenant_id)
            .where(TenantLocation.tenant_id == tenant.id)
            .where(TenantLocation.retell_agent_id.is_not(None))
            .where(TenantLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None

        return TenantResponse.from_tenant(tenant, user=tenant_user, has_retell_secret=has_retell)


@router.patch("/{slug}", response_model=TenantResponse)
@audit(
    AuditAction.TENANT_UPDATE, 
    resource=lambda request, slug, data, _: f"slug:{slug}",
    actor=AuditActor.ADMIN
)
async def update_tenant(
    request: Request,
    slug: str,
    data: TenantUpdate,
    _: User = Depends(get_current_admin),
):
    """Update tenant by slug."""
    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_slug(slug)
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{slug}' not found"
            )
        
        # Only update fields that were explicitly sent in the request.
        # Using exclude_unset=True means omitted fields are ignored,
        # but explicitly sending null will clear the value (e.g., remove an API key).
        updates = data.model_dump(exclude_unset=True)
        tenant = await service.update(tenant, **updates)
        
        from src.app.models.tenant_location import TenantLocation
        retell_result = await session.execute(
            select(TenantLocation.tenant_id)
            .where(TenantLocation.tenant_id == tenant.id)
            .where(TenantLocation.retell_agent_id.is_not(None))
            .where(TenantLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None
        
        return TenantResponse.from_tenant(tenant, has_retell_secret=has_retell)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
@audit(
    AuditAction.TENANT_DELETE, 
    resource=lambda request, slug, hard, _: f"slug:{slug}",
    actor=AuditActor.ADMIN
)
async def delete_tenant(
    request: Request,
    slug: str,
    hard: bool = False,
    _: User = Depends(get_current_admin),
):
    """
    Delete tenant by slug.
    
    Args:
        slug: Tenant slug
        hard: If True, permanently delete. Default is soft delete.
    """
    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_slug(slug, include_inactive=True)

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{slug}' not found"
            )

        # Initialize SupabaseService for cleaning up Supabase auth users
        supabase_service = None
        if hard:
            from src.app.services.supabase_service import SupabaseService
            supabase_service = SupabaseService()
        
        await service.delete(tenant, hard_delete=hard, supabase_service=supabase_service)


class ResendInviteRequest(BaseModel):
    email: str = Field(..., description="Email of the user to re-invite")


@router.post("/{slug}/reinvite", status_code=status.HTTP_200_OK)
async def reinvite_tenant_user(
    slug: str,
    data: ResendInviteRequest,
    _: User = Depends(get_current_admin),
):
    """
    Re-invite a tenant user via Supabase.

    Deletes the old Supabase auth user (if any) and creates a fresh invite.
    Use this when the original invite link expired or the Supabase user was
    accidentally deleted.
    """
    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_slug(slug, include_inactive=True)

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{slug}' not found"
            )

        # Find the local user
        result = await session.execute(
            select(User).where(User.email == data.email, User.tenant_id == tenant.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{data.email}' not found for tenant '{slug}'"
            )

        supabase_service = SupabaseService()

        # Delete old Supabase auth user (user.id IS the Supabase UUID)
        try:
            supabase_service.delete_user(user.id)
            logger.info(f"Deleted old Supabase user {user.id} for re-invite")
        except Exception as e:
            logger.warning(f"Could not delete old Supabase user {user.id}: {e}")

        # Send fresh invite
        try:
            response = supabase_service.invite_user(
                email=data.email,
                tenant_id=str(tenant.id),
                role=user.role
            )
            new_supabase_id = None
            if hasattr(response, 'user') and hasattr(response.user, 'id'):
                new_supabase_id = str(response.user.id)
            elif isinstance(response, dict) and 'id' in response:
                new_supabase_id = str(response['id'])

            if not new_supabase_id:
                raise ValueError("Supabase invite did not return a user ID")

            # PK changed — delete old row, create new one with new Supabase UUID
            old_role = user.role
            old_tenant_id = user.tenant_id
            old_is_active = user.is_active
            await session.delete(user)
            await session.flush()

            new_user = User(
                id=new_supabase_id,
                email=data.email,
                role=old_role,
                tenant_id=old_tenant_id,
                is_active=old_is_active,
            )
            session.add(new_user)
        except Exception as e:
            logger.error(f"Re-invite failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to re-invite user: {e}"
            )

    return {"message": f"Invite re-sent to {data.email}"}


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


class LocationResponse(BaseModel):
    """Response model for a location (no secrets)."""
    id: str
    tenant_id: str
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

    model_config = {"from_attributes": True}

    @classmethod
    def from_location(cls, loc: Any) -> "LocationResponse":
        return cls(
            id=str(loc.id),
            tenant_id=str(loc.tenant_id),
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
        )


# =============================================================================
# Location Routes
# =============================================================================

@router.post("/{slug}/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.LOCATION_CREATE,
    resource=lambda request, slug, data, _: f"tenant:{slug}/location:{data.slug}",
    actor=AuditActor.ADMIN,
)
async def create_location(
    request: Request,
    slug: str,
    data: LocationCreate,
    _: User = Depends(get_current_admin),
):
    """Create a new location under a tenant."""
    async with get_db_session() as session:
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        existing = await service.get_location_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Location with slug '{data.slug}' already exists",
            )

        location_data = data.model_dump()
        try:
            location = await service.create_location(tenant.id, **location_data)
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
    """List all locations for a tenant."""
    async with get_db_session() as session:
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        locations = await service.list_locations(tenant.id, include_inactive=include_inactive)
        return [LocationResponse.from_location(loc) for loc in locations]


@router.get("/{slug}/locations/{loc_slug}", response_model=LocationResponse)
async def get_location(
    slug: str,
    loc_slug: str,
    _: User = Depends(get_current_admin),
):
    """Get a specific location by slug."""
    async with get_db_session() as session:
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        location = await service.get_location_by_slug(loc_slug)
        if not location or location.tenant_id != tenant.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        return LocationResponse.from_location(location)


@router.patch("/{slug}/locations/{loc_slug}", response_model=LocationResponse)
@audit(
    AuditAction.LOCATION_UPDATE,
    resource=lambda request, slug, loc_slug, data, _: f"tenant:{slug}/location:{loc_slug}",
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
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        location = await service.get_location_by_slug(loc_slug)
        if not location or location.tenant_id != tenant.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        updates = data.model_dump(exclude_unset=True)
        location = await service.update_location(location, **updates)
        return LocationResponse.from_location(location)


@router.delete("/{slug}/locations/{loc_slug}", status_code=status.HTTP_204_NO_CONTENT)
@audit(
    AuditAction.LOCATION_DELETE,
    resource=lambda request, slug, loc_slug, hard, _: f"tenant:{slug}/location:{loc_slug}",
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
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        location = await service.get_location_by_slug(loc_slug)
        if not location or location.tenant_id != tenant.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        await service.delete_location(location, hard=hard)


@router.post("/{slug}/locations/{loc_slug}/sync")
@audit(
    AuditAction.LOCATION_SYNC,
    resource=lambda request, slug, loc_slug, _: f"tenant:{slug}/location:{loc_slug}",
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
        service = TenantService(session)

        tenant = await service.get_by_slug(slug, include_inactive=True)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{slug}' not found")

        location = await service.get_location_by_slug(loc_slug)
        if not location or location.tenant_id != tenant.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Location '{loc_slug}' not found")

        sync_service = SyncService(session)
        result = await sync_service.sync_location(tenant, location)

        return {
            "location": loc_slug,
            "success": result.success,
            "providers_synced": result.providers_synced,
            "appointment_types_synced": result.appointment_types_synced,
            "errors": result.errors,
        }


@router.get("/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_all_audit_logs(
    _: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    tenant_id: str | None = Query(None, description="Optional tenant ID to filter logs")
):
    """
    Get audit logs across all tenants (Admin only).
    """
    from sqlalchemy import select, func
    from src.app.models.audit_log import AuditLog
    
    async with get_db_session() as session:
        # Base queries
        count_stmt = select(func.count()).select_from(AuditLog)
        data_stmt = select(AuditLog)
        
        # Apply tenant filter if provided
        if tenant_id:
            count_stmt = count_stmt.where(AuditLog.tenant_id == tenant_id)
            data_stmt = data_stmt.where(AuditLog.tenant_id == tenant_id)
            
        # Get total count
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0
        
        # Get paginated data ordered newest first
        result = await session.execute(
            data_stmt
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = result.scalars().all()
        
        import math
        pages = math.ceil(total / size) if size > 0 else 0
        
        return AuditLogPaginatedResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages
        )

