"""Token management for NexHealth API authentication.

Two cache implementations:

  - ``InMemoryTokenCache``: per-process. Fine for tests and the
    single-worker local dev loop. Useless beyond that — every gunicorn
    worker, every Fargate task maintains its own copy and they all
    re-authenticate independently, multiplying the load on NexHealth's
    auth endpoint.

  - ``RedisTokenCache``: cluster-wide. Cached token shared across all
    workers + tasks. Refreshes are guarded by a distributed lock
    (``SET NX EX``) so a thundering herd of workers all racing on a
    just-expired token results in exactly one auth call, not N.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


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


# How long before NexHealth's stated expiry we consider the token stale.
# Mirrors the in-memory cache's safety margin.
_REFRESH_SAFETY_MARGIN_S = 300

# Lock TTL — long enough to cover an auth round-trip, short enough that
# a crashed lock-holder doesn't block other workers indefinitely.
_REFRESH_LOCK_TTL_S = 30


class RedisTokenCache:
    """Cluster-wide token cache backed by Redis.

    Reads and writes are non-blocking; the distributed lock around
    ``set()`` is acquired ONLY by the worker that's actively refreshing,
    so concurrent ``get()`` calls don't serialise on it.

    Resilience: any Redis error in ``get()`` returns ``None`` (cache
    miss → caller refreshes). Errors in ``set()`` are swallowed —
    we'd rather lose the cache write than block the request that
    obtained the token.
    """

    def __init__(
        self,
        async_redis: Any,
        *,
        key: str = "nh:token",
        safety_margin_s: int = _REFRESH_SAFETY_MARGIN_S,
    ) -> None:
        self._redis = async_redis
        self._key = key
        self._safety_margin_s = safety_margin_s

    async def get(self) -> str | None:
        try:
            raw = await self._redis.get(self._key)
        except Exception as exc:  # noqa: BLE001 — fail-soft on cache reads
            logger.warning(
                "Redis token cache read failed (treating as miss): %s",
                type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def set(self, token: str, expires_in: int) -> None:
        ttl = max(60, expires_in - self._safety_margin_s)
        try:
            await self._redis.set(self._key, token, ex=ttl)
        except Exception as exc:  # noqa: BLE001 — fail-soft on cache writes
            logger.warning(
                "Redis token cache write failed (next request will refresh): %s",
                type(exc).__name__,
            )


class TokenManager:
    """Manages authentication token lifecycle.

    A thread/coroutine-local lock collapses parallel refresh attempts
    *within one process* down to one auth call. Across processes, the
    Redis cache + the optional ``refresh_lock_redis`` are what stop the
    thundering herd.
    """

    def __init__(
        self,
        cache: TokenCache | None = None,
        *,
        refresh_lock_redis: Any | None = None,
        refresh_lock_key: str = "nh:token:refresh-lock",
    ) -> None:
        self._cache = cache or InMemoryTokenCache()
        self._refresh_lock_redis = refresh_lock_redis
        self._refresh_lock_key = refresh_lock_key
        # In-process: stops two coroutines in the same worker from each
        # firing fetch_token() during a refresh window.
        self._local_refresh_lock = asyncio.Lock()

    async def get_valid_token(
        self, fetch_token: Callable[[], Awaitable[tuple[str, int]]]
    ) -> str:
        """Get a valid token, fetching if necessary."""
        cached = await self._cache.get()
        if cached:
            return cached

        async with self._local_refresh_lock:
            # Double-check after acquiring the lock — another coroutine in
            # this worker may have refreshed while we were waiting.
            cached = await self._cache.get()
            if cached:
                return cached

            if self._refresh_lock_redis is not None:
                # Distributed lock: the FIRST worker across the cluster
                # that gets here actually fetches; everyone else polls
                # the cache for the result.
                got_lock = await self._try_acquire_distributed_lock()
                if not got_lock:
                    return await self._wait_for_other_worker_refresh(
                        fetch_token
                    )
                try:
                    return await self._fetch_and_cache(fetch_token)
                finally:
                    await self._release_distributed_lock()

            return await self._fetch_and_cache(fetch_token)

    async def _fetch_and_cache(
        self, fetch_token: Callable[[], Awaitable[tuple[str, int]]]
    ) -> str:
        token, expires_in = await fetch_token()
        await self._cache.set(token, expires_in)
        return token

    async def _try_acquire_distributed_lock(self) -> bool:
        try:
            # SET NX EX: returns True only if the key didn't exist.
            return bool(
                await self._refresh_lock_redis.set(
                    self._refresh_lock_key,
                    "1",
                    nx=True,
                    ex=_REFRESH_LOCK_TTL_S,
                )
            )
        except Exception as exc:  # noqa: BLE001 — fail-open: act as if we got it
            logger.warning(
                "Distributed token refresh lock unreachable (proceeding "
                "without it): %s",
                type(exc).__name__,
            )
            return True

    async def _release_distributed_lock(self) -> None:
        try:
            await self._refresh_lock_redis.delete(self._refresh_lock_key)
        except Exception:  # noqa: BLE001 — release-best-effort
            logger.debug("Token refresh lock release failed; will TTL out")

    async def _wait_for_other_worker_refresh(
        self,
        fetch_token: Callable[[], Awaitable[tuple[str, int]]],
        *,
        poll_interval_s: float = 0.2,
        max_wait_s: float = float(_REFRESH_LOCK_TTL_S),
    ) -> str:
        """Another worker holds the refresh lock — poll the cache for its
        result, falling back to a local fetch if it never appears."""
        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            await asyncio.sleep(poll_interval_s)
            cached = await self._cache.get()
            if cached:
                return cached
        # The lock-holder crashed or our cache reads keep failing —
        # fall back to a local fetch so this request still completes.
        logger.warning(
            "Timed out waiting for distributed token refresh; "
            "fetching locally as a fallback."
        )
        return await self._fetch_and_cache(fetch_token)

    async def invalidate(self) -> None:
        """Invalidate cached token (force refresh on next request)."""
        if isinstance(self._cache, InMemoryTokenCache):
            self._cache._token = None
            self._cache._expires_at = 0.0
