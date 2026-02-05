"""Admin API routes for tenant management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.dependencies import require_admin_api_key
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.auth import AuthService
from src.app.services.tenant_service import TenantService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tenants", tags=["Admin - Tenants"])


# =============================================================================
# Request/Response Models
# =============================================================================

class TenantCreate(BaseModel):
    """Request body for creating a tenant."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    
    # Optional: Initial Admin User
    admin_email: str | None = Field(None, description="Email for the initial tenant admin user")
    admin_password: str | None = Field(None, min_length=8, description="Password for the initial tenant admin user")
    
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


class TenantAdminResponse(BaseModel):
    """Admin user details in tenant response."""
    id: str
    email: str
    role: str
    is_active: bool


class TenantResponse(BaseModel):
    """Response model for tenant (no secrets)."""
    id: str
    name: str
    slug: str
    is_active: bool
    
    # Non-secret config
    nexhealth_subdomain: str | None
    nexhealth_location_id: str | None
    ghl_location_id: str | None
    ghl_custom_fields: dict[str, Any] | None
    retell_agent_id: str | None
    sikka_office_id: str | None
    
    # Credential presence indicators
    has_nexhealth_key: bool
    has_ghl_key: bool
    has_retell_secret: bool
    has_sikka_credentials: bool
    
    # Optional: Created admin user
    admin_user: TenantAdminResponse | None = None
    
    class Config:
        from_attributes = True


def tenant_to_response(tenant, admin_user=None) -> TenantResponse:
    """Convert Tenant model to response (no secrets exposed)."""
    admin_resp = None
    if admin_user:
        admin_resp = TenantAdminResponse(
            id=admin_user.id,
            email=admin_user.email,
            role=admin_user.role,
            is_active=admin_user.is_active
        )

    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        nexhealth_subdomain=tenant.nexhealth_subdomain,
        nexhealth_location_id=tenant.nexhealth_location_id,
        ghl_location_id=tenant.ghl_location_id,
        ghl_custom_fields=tenant.ghl_custom_fields,
        retell_agent_id=tenant.retell_agent_id,
        sikka_office_id=tenant.sikka_office_id,
        has_nexhealth_key=tenant.nexhealth_api_key_encrypted is not None,
        has_ghl_key=tenant.ghl_api_key_encrypted is not None,
        has_retell_secret=tenant.retell_api_secret_encrypted is not None,
        has_sikka_credentials=(
            tenant.sikka_app_id_encrypted is not None and 
            tenant.sikka_app_secret_encrypted is not None
        ),
        admin_user=admin_resp
    )


# =============================================================================
# Routes
# =============================================================================

@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    include_inactive: bool = False,
    _: str = Depends(require_admin_api_key),
):
    """List all tenants."""
    async with get_db_session() as session:
        service = TenantService(session)
        tenants = await service.list_all(include_inactive=include_inactive)
        return [tenant_to_response(t) for t in tenants]


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
@audit(
    AuditAction.TENANT_CREATE, 
    resource=lambda request, data, _: f"slug:{data.slug}",
    actor=AuditActor.ADMIN 
)
async def create_tenant(
    request: Request,
    data: TenantCreate,
    _: str = Depends(require_admin_api_key),
):
    """Create a new tenant, optionally with an initial admin user."""
    async with get_db_session() as session:
        service = TenantService(session)
        auth_service = AuthService()  # For password hashing
        
        # Check if slug already exists
        existing = await service.get_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with slug '{data.slug}' already exists"
            )
        
        # Prepare tenant data (exclude admin fields)
        tenant_data = data.model_dump(exclude={"admin_email", "admin_password"})
        
        # Create tenant
        tenant = await service.create(**tenant_data)
        
        # Create admin user if requested
        admin_user = None
        if data.admin_email and data.admin_password:
            # Check if email is available (simple check, assume email unique globally for now)
            # Ideally this should check User table but service methods might be limited
            # We'll use direct session for this special case
            from sqlalchemy import select
            
            existing_user = await session.execute(
                select(User).where(User.email == data.admin_email)
            )
            if existing_user.scalar_one_or_none():
                 raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"User with email '{data.admin_email}' already exists"
                )

            admin_user = User(
                email=data.admin_email,
                hashed_password=auth_service.get_password_hash(data.admin_password),
                role=UserRole.TENANT.value,
                tenant_id=tenant.id,
                is_active=True
            )
            session.add(admin_user)
            await session.flush() # Get ID
            await session.commit() # Commit both tenant and user

        return tenant_to_response(tenant, admin_user)


@router.get("/{slug}", response_model=TenantResponse)
async def get_tenant(
    slug: str,
    _: str = Depends(require_admin_api_key),
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
        
        return tenant_to_response(tenant)


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
    _: str = Depends(require_admin_api_key),
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
        return tenant_to_response(tenant)


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
    _: str = Depends(require_admin_api_key),
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
        
        await service.delete(tenant, hard_delete=hard)
