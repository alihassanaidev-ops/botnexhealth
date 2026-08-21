"""FastAPI dependencies for dependency injection."""

from dataclasses import dataclass
import logging
from typing import Any

from src.app.config import settings
from src.app.nexhealth.client import NexHealthClient
from src.app.nexhealth.rate_limit import NexHealthRateLimiter
from src.app.nexhealth.token_manager import RedisTokenCache, TokenManager

logger = logging.getLogger(__name__)

# Global client singletons
_nexhealth_client: NexHealthClient | None = None
_nexhealth_clients_by_key: dict[str, NexHealthClient] = {}
_nexhealth_rate_limiter: NexHealthRateLimiter | None = None
_nexhealth_rate_limiter_redis: Any | None = None
_nexhealth_token_redis_by_key: dict[str, Any] = {}


@dataclass(frozen=True)
class NexHealthCredentialContext:
    """Resolved NexHealth credential for one request/tenant."""

    mode: str
    api_key: str
    api_key_hash: str
    institution_id: str | None = None


@dataclass(frozen=True)
class NexHealthClientConfig:
    """AuthConfig-compatible wrapper for a selected NexHealth API key."""

    api_key: str
    base_url: str
    nexhealth_api_contract: Any
    nexhealth_max_keepalive_connections: int
    nexhealth_max_connections: int

    @property
    def accept_header(self) -> str:
        return self.nexhealth_api_contract.accept_header

    @property
    def api_version(self) -> str:
        return self.nexhealth_api_contract.api_version_header


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


def _build_nexhealth_token_manager(
    api_key_hash: str,
) -> tuple[TokenManager, Any | None]:
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
    cache = RedisTokenCache(redis_client, key=f"nh:token:{api_key_hash}")
    manager = TokenManager(
        cache=cache,
        refresh_lock_redis=redis_client,
        refresh_lock_key=f"nh:token:{api_key_hash}:refresh-lock",
    )
    return manager, redis_client


def resolve_nexhealth_credential(institution: Any | None = None) -> NexHealthCredentialContext:
    """Resolve the NexHealth key for an institution with platform fallback."""
    raw_key = None
    institution_id = None
    if institution is not None:
        institution_id = str(getattr(institution, "id", "")) or None
        encrypted = getattr(institution, "nexhealth_api_key_encrypted", None)
        if encrypted:
            raw_key = institution.nexhealth_api_key

    mode = "institution" if raw_key else "platform"
    api_key = raw_key or settings.nexhealth_api_key
    if not api_key:
        source = "institution or platform" if institution is not None else "platform"
        raise RuntimeError(f"NexHealth API key is not configured for {source}")

    api_key_hash = NexHealthRateLimiter.hash_api_key(api_key)
    return NexHealthCredentialContext(
        mode=mode,
        api_key=api_key,
        api_key_hash=api_key_hash,
        institution_id=institution_id,
    )


async def get_nexhealth_client_for_credential(
    credential: NexHealthCredentialContext,
) -> NexHealthClient:
    """Return a process-local NexHealth client for a selected API key."""
    global _nexhealth_rate_limiter, _nexhealth_rate_limiter_redis

    if _nexhealth_rate_limiter is None:
        (
            _nexhealth_rate_limiter,
            _nexhealth_rate_limiter_redis,
        ) = _build_nexhealth_rate_limiter()

    client = _nexhealth_clients_by_key.get(credential.api_key_hash)
    if client is not None:
        return client

    token_manager, token_redis = _build_nexhealth_token_manager(credential.api_key_hash)
    if token_redis is not None:
        _nexhealth_token_redis_by_key[credential.api_key_hash] = token_redis

    config = NexHealthClientConfig(
        api_key=credential.api_key,
        base_url=settings.nexhealth_base_url,
        nexhealth_api_contract=settings.nexhealth_api_contract,
        nexhealth_max_keepalive_connections=settings.nexhealth_max_keepalive_connections,
        nexhealth_max_connections=settings.nexhealth_max_connections,
    )
    client = NexHealthClient(
        config=config,
        token_manager=token_manager,
        rate_limiter=_nexhealth_rate_limiter,
    )
    await client.__aenter__()
    _nexhealth_clients_by_key[credential.api_key_hash] = client
    return client


async def get_nexhealth_client_for_institution(institution: Any) -> NexHealthClient:
    """Return the NexHealth client selected for an institution."""
    return await get_nexhealth_client_for_credential(
        resolve_nexhealth_credential(institution)
    )


async def init_nexhealth_client() -> None:
    """Initialize the global NexHealth client."""
    global _nexhealth_client
    global _nexhealth_rate_limiter, _nexhealth_rate_limiter_redis
    if _nexhealth_client is None:
        if _nexhealth_rate_limiter is None:
            (
                _nexhealth_rate_limiter,
                _nexhealth_rate_limiter_redis,
            ) = _build_nexhealth_rate_limiter()
        credential = resolve_nexhealth_credential(None)
        _nexhealth_client = await get_nexhealth_client_for_credential(credential)


async def cleanup_nexhealth_client() -> None:
    """Cleanup the global NexHealth client."""
    global _nexhealth_client
    global _nexhealth_rate_limiter, _nexhealth_rate_limiter_redis
    for client in list(_nexhealth_clients_by_key.values()):
        await client.__aexit__(None, None, None)
    _nexhealth_clients_by_key.clear()
    _nexhealth_client = None
    if _nexhealth_rate_limiter_redis is not None:
        try:
            await _nexhealth_rate_limiter_redis.aclose()
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.debug("Ignoring nexhealth rate limiter redis close error")
        _nexhealth_rate_limiter_redis = None
    for redis_client in list(_nexhealth_token_redis_by_key.values()):
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.debug("Ignoring nexhealth token redis close error")
    _nexhealth_token_redis_by_key.clear()
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
