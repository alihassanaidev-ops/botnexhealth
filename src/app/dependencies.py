"""FastAPI dependencies for dependency injection."""

import logging
from typing import Any

from src.app.config import settings
from src.app.nexhealth.client import NexHealthClient
from src.app.nexhealth.rate_limit import NexHealthRateLimiter
from src.app.nexhealth.token_manager import RedisTokenCache, TokenManager

logger = logging.getLogger(__name__)

# Global client singletons
_nexhealth_client: NexHealthClient | None = None
_nexhealth_rate_limiter: NexHealthRateLimiter | None = None
_nexhealth_rate_limiter_redis: Any | None = None
_nexhealth_token_redis: Any | None = None


# =============================================================================
# NexHealth Client
# =============================================================================


def _build_nexhealth_rate_limiter() -> tuple[NexHealthRateLimiter | None, Any | None]:
    """Construct the cluster-wide NexHealth rate limiter, if Redis is configured.

    Without Redis (rare, only the local-dev path) we return None and the
    HTTP client skips pre-flight limiting; the reactive 429 retry still
    applies. In any environment that hits real NexHealth traffic the
    Redis URL is required upstream (the in-process app rate limiter
    refuses to start without it in production), so this graceful skip
    only matters for tests.
    """
    redis_url = settings.effective_redis_url
    if not redis_url:
        return None, None

    from redis.asyncio import from_url as async_from_url

    redis_client = async_from_url(redis_url, decode_responses=False)
    return NexHealthRateLimiter(redis_client), redis_client


def _build_nexhealth_token_manager() -> tuple[TokenManager, Any | None]:
    """Token manager with Redis-backed cache + distributed refresh lock.

    Falls back to in-memory when Redis isn't configured. We deliberately
    use a dedicated Redis client (not the rate-limiter's) so token
    operations don't share connections with the per-request limiter eval
    calls — keeps lock contention diagnosable.
    """
    redis_url = settings.effective_redis_url
    if not redis_url:
        return TokenManager(), None

    from redis.asyncio import from_url as async_from_url

    redis_client = async_from_url(redis_url, decode_responses=False)
    cache = RedisTokenCache(redis_client)
    manager = TokenManager(cache=cache, refresh_lock_redis=redis_client)
    return manager, redis_client


async def init_nexhealth_client() -> None:
    """Initialize the global NexHealth client."""
    global _nexhealth_client
    global _nexhealth_rate_limiter, _nexhealth_rate_limiter_redis
    global _nexhealth_token_redis
    if _nexhealth_client is None:
        if _nexhealth_rate_limiter is None:
            (
                _nexhealth_rate_limiter,
                _nexhealth_rate_limiter_redis,
            ) = _build_nexhealth_rate_limiter()
        token_manager, _nexhealth_token_redis = _build_nexhealth_token_manager()
        _nexhealth_client = NexHealthClient(
            config=settings,
            token_manager=token_manager,
            rate_limiter=_nexhealth_rate_limiter,
        )
        await _nexhealth_client.__aenter__()


async def cleanup_nexhealth_client() -> None:
    """Cleanup the global NexHealth client."""
    global _nexhealth_client
    global _nexhealth_rate_limiter, _nexhealth_rate_limiter_redis
    global _nexhealth_token_redis
    if _nexhealth_client:
        await _nexhealth_client.__aexit__(None, None, None)
        _nexhealth_client = None
    for client_ref in ("_nexhealth_rate_limiter_redis", "_nexhealth_token_redis"):
        ref = globals().get(client_ref)
        if ref is not None:
            try:
                await ref.aclose()
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.debug("Ignoring redis client close error: %s", client_ref)
            globals()[client_ref] = None
    _nexhealth_rate_limiter = None


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
