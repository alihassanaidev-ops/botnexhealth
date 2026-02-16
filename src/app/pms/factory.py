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

# Cache of initialized adapters keyed by tenant ID or "tenant_id:location_id"
_adapter_cache: dict[str, PMSAdapter] = {}


async def get_adapter_for_tenant(tenant: "Tenant") -> PMSAdapter:
    """Create (or retrieve cached) PMS adapter for a tenant (backward compat)."""
    cached = _adapter_cache.get(tenant.id)
    if cached is not None:
        return cached


    adapter: PMSAdapter

    from src.app.config import settings

    if tenant.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(tenant)
    elif tenant.sikka_app_id:
        from src.app.pms.sikka.adapter import SikkaAdapter
        adapter = await SikkaAdapter.create(tenant)
    else:
        raise ValueError(f"No PMS configured for tenant {tenant.slug}")

    _adapter_cache[tenant.id] = adapter
    logger.info(f"Created {adapter.source} adapter for tenant '{tenant.slug}'")
    return adapter


async def get_adapter_for_tenant_location(tenant: "Tenant", location: "TenantLocation") -> PMSAdapter:
    """Create (or retrieve cached) PMS adapter scoped to a specific location."""
    cache_key = f"{tenant.id}:{location.id}"
    cached = _adapter_cache.get(cache_key)
    if cached is not None:
        return cached

    adapter: PMSAdapter

    from src.app.config import settings

    if tenant.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(tenant, location=location)
    elif tenant.sikka_app_id:
        from src.app.pms.sikka.adapter import SikkaAdapter
        adapter = await SikkaAdapter.create(tenant)
    else:
        raise ValueError(f"No PMS configured for tenant {tenant.slug}")

    _adapter_cache[cache_key] = adapter
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


async def cleanup_adapters() -> None:
    """Cleanup all cached adapters on shutdown."""
    global _adapter_cache
    for tid, adapter in _adapter_cache.items():
        try:
            await adapter.close()
        except Exception:
            pass
    _adapter_cache.clear()
    logger.info("Cleaned up all PMS adapter caches")


def invalidate_adapter(tenant_id: str, location_id: str | None = None) -> None:
    """Remove a tenant's (or specific location's) cached adapter."""
    if location_id:
        _adapter_cache.pop(f"{tenant_id}:{location_id}", None)
    else:
        # Invalidate all adapters for this tenant (tenant-level + all locations)
        keys_to_remove = [k for k in _adapter_cache if k == tenant_id or k.startswith(f"{tenant_id}:")]
        for k in keys_to_remove:
            _adapter_cache.pop(k, None)
