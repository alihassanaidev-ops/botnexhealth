"""
Tenant portal routes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.app.api.deps import get_current_active_user
from src.app.api.models import TenantResponse
from src.app.database import get_db_session
from src.app.models.user import User, UserRole
from src.app.services.tenant_service import TenantService

router = APIRouter(prefix="/tenant", tags=["Tenant Portal"])


@router.get("/me", response_model=TenantResponse)
async def get_my_tenant_config(
    current_user: Annotated[User, Depends(get_current_active_user)]
):
    """
    Get configuration for the current user's tenant.
    
    Returns masked credentials (booleans only).
    """
    # Verify user is a TENANT user or at least has a tenant_id
    if not current_user.tenant_id:
        # Admins or users without tenant_id cannot use this endpoint
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="User is not associated with a tenant"
        )

    async with get_db_session() as session:
        service = TenantService(session)
        tenant = await service.get_by_id(current_user.tenant_id)
        
        if not tenant:
            # Should not happen unless referential integrity is broken
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found"
            )
            
        from src.app.models.tenant_location import TenantLocation
        from sqlalchemy import select
        retell_result = await session.execute(
            select(TenantLocation.tenant_id)
            .where(TenantLocation.tenant_id == tenant.id)
            .where(TenantLocation.retell_agent_id.is_not(None))
            .where(TenantLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None
            
        return TenantResponse.from_tenant(tenant, current_user, has_retell_secret=has_retell)
