"""FastAPI dependencies for dependency injection."""

import logging

from src.app.config import Settings, SikkaConfig, settings
from src.app.nexhealth.client import NexHealthClient
from src.app.sikka.client import SikkaClient

logger = logging.getLogger(__name__)

# Global client singletons
_nexhealth_client: NexHealthClient | None = None
_sikka_client: SikkaClient | None = None


# =============================================================================
# NexHealth Client
# =============================================================================

async def init_nexhealth_client() -> None:
    """Initialize the global NexHealth client."""
    global _nexhealth_client
    if _nexhealth_client is None:
        _nexhealth_client = NexHealthClient(config=settings)
        await _nexhealth_client.__aenter__()


async def cleanup_nexhealth_client() -> None:
    """Cleanup the global NexHealth client."""
    global _nexhealth_client
    if _nexhealth_client:
        await _nexhealth_client.__aexit__(None, None, None)
        _nexhealth_client = None


async def get_nexhealth_client_dependency() -> NexHealthClient:
    """
    FastAPI dependency that provides the global singleton NexHealth client.

    This ensures that the token manager (and its cache) persists across requests.
    """
    if _nexhealth_client is None:
        await init_nexhealth_client()

    if _nexhealth_client is None:
        raise RuntimeError("NexHealth client not initialized")

    return _nexhealth_client


# =============================================================================
# Sikka Client
# =============================================================================

async def init_sikka_client() -> None:
    """Initialize the global Sikka client."""
    global _sikka_client

    # Only initialize if Sikka credentials are configured
    if not settings.sikka_app_id or not settings.sikka_app_secret:
        logger.info("Sikka credentials not configured, skipping Sikka client initialization")
        return

    if _sikka_client is None:
        sikka_config = SikkaConfig(settings)
        _sikka_client = SikkaClient(config=sikka_config)
        await _sikka_client.__aenter__()
        logger.info("Sikka client initialized")


async def cleanup_sikka_client() -> None:
    """Cleanup the global Sikka client."""
    global _sikka_client
    if _sikka_client:
        await _sikka_client.__aexit__(None, None, None)
        _sikka_client = None


async def get_sikka_client_dependency() -> SikkaClient | None:
    """
    FastAPI dependency that provides the global singleton Sikka client.

    Returns None if Sikka is not configured.
    """
    if _sikka_client is None:
        # Try to initialize if not yet done
        await init_sikka_client()

    return _sikka_client


# =============================================================================
# Settings
# =============================================================================

def get_settings() -> Settings:
    """Dependency for application settings."""
    return settings


# =============================================================================
# Admin API Key Authentication
# =============================================================================

from fastapi import Header, HTTPException, status


async def require_admin_api_key(
    x_admin_api_key: str = Header(..., alias="x-admin-api-key")
) -> str:
    """
    Validate admin API key from request header.
    
    Raises HTTPException 401 if key is missing or invalid.
    """
    if x_admin_api_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key"
        )
    return x_admin_api_key


# =============================================================================
# Tenant-Aware Dependencies
# =============================================================================

from fastapi import Request
from typing import Optional


async def get_current_tenant(request: Request) -> Optional["Tenant"]:
    """
    Get current tenant from request state (set by TenantMiddleware).
    
    Returns None if no tenant is set (public endpoints).
    """
    # Import here to avoid circular imports
    from src.app.models.tenant import Tenant
    
    tenant = getattr(request.state, "tenant", None)
    return tenant


async def require_tenant(request: Request) -> "Tenant":
    """
    Require a tenant to be present in the request.
    
    Raises HTTPException 400 if no tenant found.
    """
    from src.app.models.tenant import Tenant
    
    tenant = await get_current_tenant(request)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Slug header is required for this endpoint"
        )
    return tenant


async def get_tenant_nexhealth_client(request: Request) -> NexHealthClient:
    """
    Get a NexHealth client configured for the current tenant.
    
    If no tenant is set or tenant has no API key, falls back to global client.
    """
    tenant = await get_current_tenant(request)
    
    # If tenant has its own NexHealth API key, create a tenant-specific client
    if tenant and tenant.nexhealth_api_key:
        # Create a tenant-specific settings object
        tenant_settings = Settings(
            nexhealth_api_key=tenant.nexhealth_api_key,
            nexhealth_subdomain=tenant.nexhealth_subdomain or settings.nexhealth_subdomain,
            nexhealth_location_id=tenant.nexhealth_location_id or settings.nexhealth_location_id,
        )
        # Create a new client for this tenant
        client = NexHealthClient(config=tenant_settings)
        await client.__aenter__()
        # Note: In production, you'd want to cache these clients per tenant
        # For now, we create a new one per request (stateless approach)
        return client
    
    # Fall back to global client
    return await get_nexhealth_client_dependency()
