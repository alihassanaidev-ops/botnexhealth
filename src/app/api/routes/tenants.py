"""Admin API routes for tenant management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.database import get_db_session
from src.app.dependencies import require_admin_api_key
from src.app.models.audit_log import AuditAction
from src.app.services.audit_decorator import audited_api
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
    
    class Config:
        from_attributes = True


def tenant_to_response(tenant) -> TenantResponse:
    """Convert Tenant model to response (no secrets exposed)."""
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
@audited_api(AuditAction.TENANT_CREATE, resource_key="slug")
async def create_tenant(
    request: Request,
    data: TenantCreate,
    _: str = Depends(require_admin_api_key),
):
    """Create a new tenant."""
    async with get_db_session() as session:
        service = TenantService(session)
        
        # Check if slug already exists
        existing = await service.get_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with slug '{data.slug}' already exists"
            )
        
        tenant = await service.create(**data.model_dump())
        return tenant_to_response(tenant)


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
@audited_api(AuditAction.TENANT_UPDATE, resource_key="slug")
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
@audited_api(AuditAction.TENANT_DELETE, resource_key="slug")
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
