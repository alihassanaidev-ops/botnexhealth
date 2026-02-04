"""Middleware for tenant context resolution."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.database import get_db_session
from src.app.services.tenant_service import TenantService

logger = logging.getLogger(__name__)

# Header for tenant identification
TENANT_HEADER = "X-Tenant-Slug"


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to resolve tenant from request header and attach to request state.
    
    The tenant is looked up by the X-Tenant-Slug header.
    For Retell webhooks, tenant is resolved by agent_id in the request body.
    """
    
    # Paths that don't require tenant context
    EXEMPT_PATHS = {
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
    
    # Paths with their own tenant resolution (e.g., Retell webhooks use agent_id)
    SELF_RESOLVING_PATHS = {
        "/webhook/retell",
    }
    
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        """Process request and resolve tenant context."""
        path = request.url.path
        
        # Skip exempt paths
        if path in self.EXEMPT_PATHS or path.startswith("/admin/"):
            return await call_next(request)
        
        # Skip self-resolving paths (they handle tenant resolution internally)
        if any(path.startswith(p) for p in self.SELF_RESOLVING_PATHS):
            return await call_next(request)
        
        # Get tenant slug from header
        tenant_slug = request.headers.get(TENANT_HEADER)
        
        if not tenant_slug:
            # No tenant header - continue without tenant (handler will decide if required)
            request.state.tenant = None
            return await call_next(request)
        
        # Lookup tenant
        try:
            async with get_db_session() as session:
                service = TenantService(session)
                tenant = await service.get_by_slug(tenant_slug)
                
                if tenant:
                    request.state.tenant = tenant
                    logger.debug(f"Resolved tenant: {tenant.slug}")
                else:
                    logger.warning(f"Tenant not found: {tenant_slug}")
                    request.state.tenant = None
        except Exception as e:
            logger.error(f"Error resolving tenant: {e}")
            request.state.tenant = None
        
        return await call_next(request)


def get_tenant_from_request(request: Request):
    """Get tenant from request state. Returns None if not set."""
    return getattr(request.state, "tenant", None)
