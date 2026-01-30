"""FastAPI dependencies for dependency injection."""

from typing import Annotated

from fastapi import Depends

from src.app.config import Settings, settings
from src.app.nexhealth.client import NexHealthClient



_nexhealth_client: NexHealthClient | None = None


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


def get_settings() -> Settings:
    """Dependency for application settings."""
    return settings


async def get_nexhealth_client_dependency() -> NexHealthClient:
    """
    FastAPI dependency that provides the global singleton NexHealth client.
    
    This ensures that the token manager (and its cache) persists across requests.
    """
    if _nexhealth_client is None:
        # Fallback if not initialized (e.g. in tests if not using full app lifecycle)
        # But for production, init_nexhealth_client should be called on startup
        await init_nexhealth_client()
        
    if _nexhealth_client is None:
        raise RuntimeError("NexHealth client not initialized")
        
    return _nexhealth_client

