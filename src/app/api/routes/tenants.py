"""Admin API routes for tenant management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.api.deps import get_current_admin
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.auth import AuthService
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
    email: str = Field(..., description="Email for the initial tenant user (used as username)")
    # password: str = Field(..., min_length=8, description="Password for the initial tenant user")  # REMOVED for Supabase Invite Flow
    
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
    """Request body for updating a tenant."""
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
    include_inactive: bool = False,
    _: User = Depends(get_current_admin),
):
    """List all tenants."""
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
    """Create a new tenant, with an initial tenant user."""
    async with get_db_session() as session:
        service = TenantService(session)
        # auth_service = AuthService()  # Not needed for password hashing anymore
        supabase_service = SupabaseService()
        
        # Check if slug already exists
        existing = await service.get_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with slug '{data.slug}' already exists"
            )
        
        # Prepare tenant data (exclude user fields)
        # Prepare tenant data (exclude user fields)
        tenant_data = data.model_dump(exclude={"email", "password", "nexhealth_api_key", "nexhealth_subdomain", "nexhealth_location_id", "ghl_api_key", "ghl_location_id", "ghl_custom_fields", "retell_agent_id", "retell_api_secret", "sikka_app_id", "sikka_app_secret", "sikka_office_id"}) # Exclude all optional fields explicitly or just user fields if create handles extras. 
        # Actually original code excluded email and password. Since password is removed from model, we just convert.
        # But wait, data.model_dump() will not have password if we removed it from model.
        tenant_data = data.model_dump(exclude={"email"})
        
        # Create tenant
        tenant = await service.create(**tenant_data)
        
        # Create initial user (Mandatory)
        # Check if email is available (simple check, assume email unique globally for now)
        # Ideally this should check User table but service methods might be limited
        # We'll use direct session for this special case
        from sqlalchemy import select
        
        existing_user = await session.execute(
            select(User).where(User.email == data.email)
        )
        if existing_user.scalar_one_or_none():
             raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{data.email}' already exists"
            )


        # Invite User via Supabase
        supabase_user_id = None
        try:
             response = supabase_service.invite_user(
                email=data.email, 
                tenant_id=tenant.id, 
                role=UserRole.TENANT.value
            )
             # Extract user ID from response if possible
             # Supabase response -> UserResponse(user=User(...))
             if hasattr(response, 'user') and hasattr(response.user, 'id'):
                 supabase_user_id = response.user.id
             elif isinstance(response, dict) and 'id' in response: # Fallback for mock/dict
                 supabase_user_id = response['id']
                 
        except Exception as e:
            # If supabase fails, we should probably rollback tenant creation?
            # Or just proceed and let admin retry invite manually? 
            # For now, we'll log it and proceed but maybe without a local user or with local user but no password set.
            # Ideally we want atomic operation. Since Supabase is external, we can't fully transaction it.
            # Best effort: If invite fails, we could raise error and rollback DB transaction.
            logging.error(f"Supabase invite failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send Supabase invite"
            )

        user = User(
            email=data.email,
            hashed_password=None, # Password set via Supabase
            role=UserRole.TENANT.value,
            tenant_id=tenant.id,
            supabase_id=supabase_user_id,  # Store for cleanup on deletion
            is_active=True
        )
        session.add(user)
        
        try:
            await session.commit() # Commit both tenant and user
        except Exception as e:
            logger.error(f"Failed to commit tenant creation to DB: {e}")
            
            # Compensating Transaction: Delete orphaned Supabase user
            if supabase_user_id:
                logger.warning(f"Initiating compensating transaction: Deleting Supabase user {supabase_user_id}")
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
    """Get tenant by slug."""
    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_slug(slug)
        
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
        
        # Only update non-None fields
        updates = {k: v for k, v in data.model_dump().items() if v is not None}
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
        tenant = await service.get_by_slug(slug)
        
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
