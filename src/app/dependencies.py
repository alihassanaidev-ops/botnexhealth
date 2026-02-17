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
