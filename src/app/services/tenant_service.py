"""Tenant service for CRUD operations and lookups."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.tenant import Tenant

logger = logging.getLogger(__name__)


class TenantService:
    """Service for tenant CRUD operations."""
    
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
    
    async def get_by_id(self, tenant_id: str) -> Tenant | None:
        """Get tenant by ID."""
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active == True)
        )
        return result.scalar_one_or_none()
    
    async def get_by_slug(self, slug: str, include_inactive: bool = False) -> Tenant | None:
        """Get tenant by slug."""
        query = select(Tenant).where(Tenant.slug == slug)
        if not include_inactive:
            query = query.where(Tenant.is_active == True)
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_by_retell_agent_id(self, agent_id: str) -> Tenant | None:
        """
        Get tenant by Retell agent ID.
        
        Used for routing Retell webhooks to the correct tenant.
        """
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.retell_agent_id == agent_id,
                Tenant.is_active == True
            )
        )
        return result.scalar_one_or_none()
    
    async def list_all(self, include_inactive: bool = False) -> list[Tenant]:
        """List all tenants."""
        query = select(Tenant)
        if not include_inactive:
            query = query.where(Tenant.is_active == True)
        query = query.order_by(Tenant.name)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def create(
        self,
        name: str,
        slug: str,
        *,
        nexhealth_api_key: str | None = None,
        nexhealth_subdomain: str | None = None,
        nexhealth_location_id: str | None = None,
        ghl_api_key: str | None = None,
        ghl_location_id: str | None = None,
        ghl_custom_fields: dict[str, Any] | None = None,
        retell_agent_id: str | None = None,
        retell_api_secret: str | None = None,
        sikka_app_id: str | None = None,
        sikka_app_secret: str | None = None,
        sikka_office_id: str | None = None,
    ) -> Tenant:
        """Create a new tenant."""
        tenant = Tenant(
            name=name,
            slug=slug,
            nexhealth_subdomain=nexhealth_subdomain,
            nexhealth_location_id=nexhealth_location_id,
            ghl_location_id=ghl_location_id,
            ghl_custom_fields=ghl_custom_fields,
            retell_agent_id=retell_agent_id,
            sikka_office_id=sikka_office_id,
        )
        
        # Set encrypted fields via properties
        tenant.nexhealth_api_key = nexhealth_api_key
        tenant.ghl_api_key = ghl_api_key
        tenant.retell_api_secret = retell_api_secret
        tenant.sikka_app_id = sikka_app_id
        tenant.sikka_app_secret = sikka_app_secret
        
        self.session.add(tenant)
        await self.session.flush()
        await self.session.refresh(tenant)
        
        logger.info(f"Created tenant: {tenant.slug} (id={tenant.id})")
        return tenant
    
    async def update(
        self,
        tenant: Tenant,
        **updates: Any
    ) -> Tenant:
        """Update tenant fields."""
        # Fields that use encryption setters
        encrypted_fields = {
            "nexhealth_api_key",
            "ghl_api_key",
            "retell_api_secret",
            "sikka_app_id",
            "sikka_app_secret",
        }
        
        for key, value in updates.items():
            if key in encrypted_fields:
                # Use property setter for encrypted fields
                setattr(tenant, key, value)
            elif hasattr(tenant, key):
                setattr(tenant, key, value)
        
        await self.session.flush()
        await self.session.refresh(tenant)
        
        logger.info(f"Updated tenant: {tenant.slug}")
        return tenant
    
    async def delete(
        self, 
        tenant: Tenant, 
        hard_delete: bool = False,
        supabase_service: Any = None
    ) -> None:
        """
        Delete a tenant.
        
        Args:
            tenant: Tenant to delete
            hard_delete: If True, permanently delete. If False, soft delete (set is_active=False).
            supabase_service: Optional SupabaseService instance for cleaning up Supabase auth users.
        """
        if hard_delete:
            # Delete associated users first to avoid foreign key constraint violation
            from src.app.models.user import User
            result = await self.session.execute(
                select(User).where(User.tenant_id == tenant.id)
            )
            users = result.scalars().all()
            
            for user in users:
                # Delete from Supabase auth (user.id IS the Supabase UUID)
                if supabase_service:
                    try:
                        supabase_service.delete_user(user.id)
                        logger.info(f"Deleted Supabase auth user {user.id} for {user.email}")
                    except Exception as e:
                        logger.warning(f"Failed to delete Supabase user {user.id}: {e}")
                        # Continue with local deletion even if Supabase fails
                
                await self.session.delete(user)
                logger.info(f"Deleted user {user.email} associated with tenant {tenant.slug}")
            
            # Flush to ensure user deletes are committed before tenant delete
            await self.session.flush()
            
            await self.session.delete(tenant)
            logger.info(f"Hard deleted tenant: {tenant.slug}")
        else:
            tenant.is_active = False
            await self.session.flush()
            logger.info(f"Soft deleted tenant: {tenant.slug}")
