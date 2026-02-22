"""Adapter factory — picks the right PMS adapter for a tenant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from src.app.pms.base import PMSAdapter

if TYPE_CHECKING:
    from src.app.models.tenant import Tenant
    from src.app.models.tenant_location import TenantLocation

logger = logging.getLogger(__name__)


async def get_adapter_for_tenant(tenant: "Tenant") -> PMSAdapter:
    """Create a fresh PMS adapter for a tenant (backward compat)."""
    adapter: PMSAdapter

    from src.app.config import settings

    if tenant.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(tenant)
    else:
        raise ValueError(f"No PMS configured for tenant {tenant.slug}")

    logger.info(f"Created {adapter.source} adapter for tenant '{tenant.slug}'")
    return adapter


async def get_adapter_for_tenant_location(tenant: "Tenant", location: "TenantLocation") -> PMSAdapter:
    """Create a fresh PMS adapter scoped to a specific location."""
    adapter: PMSAdapter

    from src.app.config import settings

    if tenant.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(tenant, location=location)
    else:
        raise ValueError(f"No PMS configured for tenant {tenant.slug}")

    logger.info(f"Created {adapter.source} adapter for tenant '{tenant.slug}' location '{location.slug}'")
    return adapter


async def get_tenant_pms(request: Request) -> PMSAdapter:
    """FastAPI dependency — resolves tenant (and optional location) from request and returns adapter."""
    from src.app.models.tenant import Tenant
    from src.app.models.tenant_location import TenantLocation

    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant context required (X-Tenant-Slug header)")

    location: TenantLocation | None = getattr(request.state, "location", None)
    if location:
        return await get_adapter_for_tenant_location(tenant, location)
    return await get_adapter_for_tenant(tenant)
