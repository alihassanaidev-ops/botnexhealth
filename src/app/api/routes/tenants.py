"""Admin API routes for tenant management."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.api.deps import get_current_admin
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.tenant_service import TenantService
from src.app.services.supabase_service import SupabaseService
from src.app.api.models import TenantResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tenants", tags=["Admin - Tenants"])


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
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

    # GoHighLevel
    ghl_api_key: str | None = None
    ghl_location_id: str | None = None
    ghl_custom_fields: dict[str, str] | None = None

    # Retell
    retell_agent_id: str | None = None
    retell_api_secret: str | None = None

    # Sikka
    sikka_app_id: str | None = None
    sikka_app_secret: str | None = None
    sikka_office_id: str | None = None


class TenantUpdate(BaseModel):
    """Request body for updating a tenant.

    Uses exclude_unset so that omitted fields are ignored,
    while explicitly sending null clears the value.
    """
    name: str | None = None
    is_active: bool | None = None

    # NexHealth
    nexhealth_api_key: str | None = None
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

    # GoHighLevel
    ghl_api_key: str | None = None
    ghl_location_id: str | None = None
    ghl_custom_fields: dict[str, str] | None = None

    # Retell
    retell_agent_id: str | None = None
    retell_api_secret: str | None = None

    # Sikka
    sikka_app_id: str | None = None
    sikka_app_secret: str | None = None
    sikka_office_id: str | None = None





# =============================================================================
# Routes
# =============================================================================

@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    include_inactive: bool = True,
    _: User = Depends(get_current_admin),
):
    """List all tenants. Admins see soft-deleted (is_active=false) tenants by default."""
    async with get_db_session() as session:
        service = TenantService(session)
        tenants = await service.list_all(include_inactive=include_inactive)
        return [TenantResponse.from_tenant(t) for t in tenants]


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
            logger.error(f"Supabase invite failed for {data.email}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send Supabase invite"
            )

        # --- Create local user record ---

        user = User(
            email=data.email,
            hashed_password=None,
            role=UserRole.TENANT.value,
            tenant_id=tenant.id,
            supabase_id=supabase_user_id,
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

        return TenantResponse.from_tenant(tenant, user)


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
        
        return TenantResponse.from_tenant(tenant)


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
        return TenantResponse.from_tenant(tenant)


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
