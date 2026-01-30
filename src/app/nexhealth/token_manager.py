"""Token management for NexHealth API authentication."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Protocol


class TokenCache(Protocol):
    """Protocol for token caching implementations."""

    async def get(self) -> str | None:
        """Get cached token if available and valid."""
        ...

    async def set(self, token: str, expires_in: int) -> None:
        """Cache token with expiration."""
        ...


class InMemoryTokenCache:
    """In-memory token cache (single process only)."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get(self) -> str | None:
        if self._token and time.time() < self._expires_at:
            return self._token
        return None

    async def set(self, token: str, expires_in: int) -> None:
        self._token = token
        # Set expiration 5 minutes before actual expiry for safety
        self._expires_at = time.time() + expires_in - 300


class TokenManager:
    """Manages authentication token lifecycle."""

    def __init__(self, cache: TokenCache | None = None) -> None:
        self._cache = cache or InMemoryTokenCache()

    async def get_valid_token(self, fetch_token: Callable[[], Awaitable[tuple[str, int]]]) -> str:
        """Get a valid token, fetching if necessary."""
        cached = await self._cache.get()
        if cached:
            return cached

        token, expires_in = await fetch_token()
        await self._cache.set(token, expires_in)
        return token

    async def invalidate(self) -> None:
        """Invalidate cached token (force refresh on next request)."""
        if isinstance(self._cache, InMemoryTokenCache):
            self._cache._token = None
            self._cache._expires_at = 0.0
