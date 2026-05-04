"""Tests for the NexHealth token manager — distributed refresh path.

The in-memory cache is exercised indirectly elsewhere; these tests pin
the Redis-backed cache + distributed lock contract so a future change
can't silently regress to "every worker re-authenticates on every cold
start".
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.app.nexhealth.token_manager import (
    InMemoryTokenCache,
    RedisTokenCache,
    TokenManager,
)


# ── RedisTokenCache ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_token_cache_decodes_bytes_value() -> None:
    """Redis returns bytes when decode_responses=False; cache must decode."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"my-stored-token")

    cache = RedisTokenCache(redis)
    assert await cache.get() == "my-stored-token"


@pytest.mark.asyncio
async def test_redis_token_cache_returns_none_on_miss() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    cache = RedisTokenCache(redis)
    assert await cache.get() is None


@pytest.mark.asyncio
async def test_redis_token_cache_get_is_fail_soft(caplog) -> None:
    """A Redis error on read MUST not propagate — caller treats it as a miss."""
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))

    cache = RedisTokenCache(redis)
    with caplog.at_level("WARNING", logger="src.app.nexhealth.token_manager"):
        assert await cache.get() is None
    assert any("token cache read failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_redis_token_cache_set_applies_safety_margin() -> None:
    """The TTL we send to Redis must be (expires_in - safety_margin), so the
    cache invalidates *before* NexHealth itself rejects the token."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    cache = RedisTokenCache(redis, safety_margin_s=300)
    await cache.set("tok", expires_in=3600)

    redis.set.assert_awaited_once()
    args, kwargs = redis.set.call_args
    # SET key value ex=...
    assert args[0] == "nh:token"
    assert args[1] == "tok"
    assert kwargs["ex"] == 3300  # 3600 - 300 safety margin


@pytest.mark.asyncio
async def test_redis_token_cache_set_floors_at_60_seconds() -> None:
    """Don't ever cache for less than 60s; otherwise a tight expires_in
    rounds to a near-zero TTL and we hammer the auth endpoint."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    cache = RedisTokenCache(redis, safety_margin_s=300)
    await cache.set("tok", expires_in=120)  # would be -180s without floor

    args, kwargs = redis.set.call_args
    assert kwargs["ex"] == 60


# ── TokenManager — distributed refresh ──────────────────────────────


@pytest.mark.asyncio
async def test_token_manager_local_lock_collapses_concurrent_refreshes() -> None:
    """Two coroutines in the same worker hitting an empty cache must NOT
    both call fetch_token() — exactly one wins, the other reads the
    cached result."""
    cache = InMemoryTokenCache()
    fetch_calls = 0

    async def slow_fetch() -> tuple[str, int]:
        nonlocal fetch_calls
        fetch_calls += 1
        await asyncio.sleep(0.01)
        return ("freshly-minted-token", 3600)

    manager = TokenManager(cache=cache)

    results = await asyncio.gather(
        manager.get_valid_token(slow_fetch),
        manager.get_valid_token(slow_fetch),
        manager.get_valid_token(slow_fetch),
    )

    assert all(r == "freshly-minted-token" for r in results)
    assert fetch_calls == 1, (
        f"Expected exactly one fetch_token call across 3 concurrent waiters, "
        f"got {fetch_calls}"
    )


@pytest.mark.asyncio
async def test_token_manager_loser_of_distributed_lock_polls_cache() -> None:
    """When this worker fails to acquire the cluster-wide lock, it must
    poll the cache for the lock-holder's result, not call fetch_token
    locally."""
    cache = AsyncMock()
    # First miss, then on the second poll the "other worker" has populated.
    cache.get = AsyncMock(side_effect=[None, None, "from-other-worker"])
    cache.set = AsyncMock(return_value=None)

    redis = AsyncMock()
    # SET NX EX returns falsy → another worker holds the lock.
    redis.set = AsyncMock(return_value=False)
    redis.delete = AsyncMock(return_value=1)

    fetch_count = 0

    async def fetch() -> tuple[str, int]:
        nonlocal fetch_count
        fetch_count += 1
        return ("local-fetch-shouldnt-run", 3600)

    manager = TokenManager(cache=cache, refresh_lock_redis=redis)
    result = await manager.get_valid_token(fetch)

    assert result == "from-other-worker"
    assert fetch_count == 0, (
        "Loser of the distributed lock fetched anyway — should have polled "
        "the cache for the lock-holder's result instead."
    )


@pytest.mark.asyncio
async def test_token_manager_winner_releases_lock_after_refresh() -> None:
    """Whoever holds the lock must release it after the cache write so
    other workers can refresh on the next expiry — including when the
    refresh raises."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=None)

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)  # we win the lock
    redis.delete = AsyncMock(return_value=1)

    async def fetch() -> tuple[str, int]:
        return ("our-fresh-token", 3600)

    manager = TokenManager(cache=cache, refresh_lock_redis=redis)
    result = await manager.get_valid_token(fetch)

    assert result == "our-fresh-token"
    redis.delete.assert_awaited_once_with("nh:token:refresh-lock")


@pytest.mark.asyncio
async def test_token_manager_distributed_lock_failure_falls_open() -> None:
    """Redis can't be reached for the SET NX EX call — the request still
    has to complete, so fall back to doing the fetch locally."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=None)

    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.delete = AsyncMock(return_value=0)

    fetch_count = 0

    async def fetch() -> tuple[str, int]:
        nonlocal fetch_count
        fetch_count += 1
        return ("local-fallback-token", 3600)

    manager = TokenManager(cache=cache, refresh_lock_redis=redis)
    result = await manager.get_valid_token(fetch)

    assert result == "local-fallback-token"
    assert fetch_count == 1
