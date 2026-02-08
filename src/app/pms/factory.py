"""Adapter factory — picks the right PMS adapter for a tenant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from src.app.pms.base import PMSAdapter

if TYPE_CHECKING:
    from src.app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# Cache of initialized adapters keyed by tenant ID
_adapter_cache: dict[str, PMSAdapter] = {}


async def get_adapter_for_tenant(tenant: "Tenant") -> PMSAdapter:
    """Create (or retrieve cached) PMS adapter for a tenant."""
    cached = _adapter_cache.get(tenant.id)
    if cached is not None:
        return cached

    adapter: PMSAdapter

    if tenant.nexhealth_api_key:
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


async def get_tenant_pms(request: Request) -> PMSAdapter:
    """FastAPI dependency — resolves tenant from request and returns adapter."""
    from src.app.models.tenant import Tenant

    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant context required (X-Tenant-Slug header)")
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


def invalidate_adapter(tenant_id: str) -> None:
    """Remove a tenant's cached adapter (e.g. after credential update)."""
    _adapter_cache.pop(tenant_id, None)
