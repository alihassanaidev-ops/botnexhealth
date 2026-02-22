"""FastAPI dependencies for dependency injection."""

import logging

from src.app.config import settings
from src.app.nexhealth.client import NexHealthClient

logger = logging.getLogger(__name__)

# Global client singletons
_nexhealth_client: NexHealthClient | None = None


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
