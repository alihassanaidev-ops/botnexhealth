"""Middleware for tenant and location context resolution."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.database import get_db_session
from src.app.services.tenant_service import TenantService

logger = logging.getLogger(__name__)

# Headers for tenant/location identification
TENANT_HEADER = "X-Tenant-Slug"
LOCATION_HEADER = "X-Location-Slug"


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to resolve tenant and location from request headers.

    - X-Tenant-Slug → request.state.tenant
    - X-Location-Slug → request.state.location (requires tenant)

    For Retell webhooks, tenant/location is resolved by agent_id in the handler.
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
        """Process request and resolve tenant + location context."""
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
            request.state.location = None
            return await call_next(request)

        # Lookup tenant (and optional location)
        try:
            async with get_db_session() as session:
                service = TenantService(session)
                tenant = await service.get_by_slug(tenant_slug)

                if tenant:
                    request.state.tenant = tenant
                    logger.debug(f"Resolved tenant: {tenant.slug}")

                    # Resolve location if header present
                    location_slug = request.headers.get(LOCATION_HEADER)
                    if location_slug:
                        location = await service.get_location_by_slug(location_slug)
                        if location and location.tenant_id == tenant.id and location.is_active:
                            request.state.location = location
                            logger.debug(f"Resolved location: {location.slug}")
                        else:
                            logger.warning(f"Location not found or not in tenant: {location_slug}")
                            request.state.location = None
                    else:
                        request.state.location = None
                else:
                    logger.warning(f"Tenant not found: {tenant_slug}")
                    request.state.tenant = None
                    request.state.location = None
        except Exception as e:
            logger.error(f"Error resolving tenant: {e}")
            request.state.tenant = None
            request.state.location = None

        return await call_next(request)


def get_tenant_from_request(request: Request):
    """Get tenant from request state. Returns None if not set."""
    return getattr(request.state, "tenant", None)


def get_location_from_request(request: Request):
    """Get location from request state. Returns None if not set."""
    return getattr(request.state, "location", None)
