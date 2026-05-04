"""Redis-backed refresh token storage for local auth sessions."""

from __future__ import annotations

import secrets

from redis.asyncio import Redis, from_url

from src.app.config import settings
from src.app.services.password_service import PasswordService


class RefreshTokenReplayError(RuntimeError):
    """Raised when a previously rotated refresh token is presented again."""


class RefreshTokenService:
    """Issue, rotate, and revoke opaque refresh tokens.

    Replay protection: every rotated token's hash is recorded for a short
    window after rotation. If a caller presents a token whose hash is in the
    revoked-rotation set, all sessions for that user are revoked and a
    :class:`RefreshTokenReplayError` is raised so the route layer can audit
    it. This is the standard pattern for refresh-token theft detection: a
    legitimate client that already received a new token will never present
    the old one again, so a replay almost always indicates an attacker.
    """

    TOKEN_BYTES = 32
    SESSION_PREFIX = "refresh"
    LOOKUP_PREFIX = "refresh_lookup"
    INDEX_PREFIX = "refresh_index"
    ACCESS_DENY_PREFIX = "access_deny"
    ACCESS_INDEX_PREFIX = "access_index"
    ROTATED_PREFIX = "refresh_rotated"
    ROTATED_TTL_SECONDS = 24 * 60 * 60  # 24h replay window

    _client: Redis | None = None

    @classmethod
    def _ttl_seconds(cls) -> int:
        return settings.refresh_token_ttl_days * 24 * 60 * 60

    @classmethod
    def _redis_url(cls) -> str:
        redis_url = settings.effective_redis_url
        if not redis_url:
            raise RuntimeError("REDIS_URL or CELERY_BROKER_URL must be configured")
        return redis_url

    @classmethod
    def _access_ttl_seconds(cls) -> int:
        return settings.access_token_ttl_minutes * 60

    @classmethod
    async def get_client(cls) -> Redis:
        if cls._client is None:
            cls._client = from_url(
                cls._redis_url(),
                encoding="utf-8",
                decode_responses=True,
            )
        return cls._client

    @classmethod
    def _token_hash(cls, token: str) -> str:
        return PasswordService.hash_token(token)

    @classmethod
    def _session_key(cls, user_id: str, token_hash: str) -> str:
        return f"{cls.SESSION_PREFIX}:{user_id}:{token_hash}"

    @classmethod
    def _lookup_key(cls, token_hash: str) -> str:
        return f"{cls.LOOKUP_PREFIX}:{token_hash}"

    @classmethod
    def _index_key(cls, user_id: str) -> str:
        return f"{cls.INDEX_PREFIX}:{user_id}"

    @classmethod
    def _access_deny_key(cls, jti: str) -> str:
        return f"{cls.ACCESS_DENY_PREFIX}:{jti}"

    @classmethod
    def _access_index_key(cls, user_id: str) -> str:
        return f"{cls.ACCESS_INDEX_PREFIX}:{user_id}"

    @classmethod
    def _rotated_key(cls, token_hash: str) -> str:
        return f"{cls.ROTATED_PREFIX}:{token_hash}"

    @classmethod
    async def issue_token(cls, user_id: str) -> str:
        token = secrets.token_urlsafe(cls.TOKEN_BYTES)
        token_hash = cls._token_hash(token)
        ttl = cls._ttl_seconds()
        client = await cls.get_client()

        await client.setex(cls._session_key(user_id, token_hash), ttl, "1")
        await client.setex(cls._lookup_key(token_hash), ttl, user_id)
        await client.sadd(cls._index_key(user_id), token_hash)
        await client.expire(cls._index_key(user_id), ttl)

        return token

    @classmethod
    async def get_user_id_for_token(cls, token: str) -> str | None:
        token_hash = cls._token_hash(token)
        client = await cls.get_client()
        user_id = await client.get(cls._lookup_key(token_hash))
        if not user_id:
            return None

        exists = await client.exists(cls._session_key(user_id, token_hash))
        if not exists:
            await client.delete(cls._lookup_key(token_hash))
            await client.srem(cls._index_key(user_id), token_hash)
            return None

        return user_id

    @classmethod
    async def rotate_token(cls, user_id: str, token: str) -> str | None:
        old_hash = cls._token_hash(token)
        client = await cls.get_client()

        # Replay detection: if the presented token's hash is already in the
        # rotated set, the legitimate client cannot be the caller — revoke
        # everything and surface the event.
        if await client.exists(cls._rotated_key(old_hash)):
            await cls.revoke_all_for_user(user_id)
            await cls.revoke_all_access_tokens_for_user(user_id)
            raise RefreshTokenReplayError(user_id)

        if not await client.exists(cls._session_key(user_id, old_hash)):
            return None

        await client.delete(
            cls._session_key(user_id, old_hash),
            cls._lookup_key(old_hash),
        )
        await client.srem(cls._index_key(user_id), old_hash)
        # Remember the rotated hash so any later replay trips detection.
        await client.setex(cls._rotated_key(old_hash), cls.ROTATED_TTL_SECONDS, user_id)

        return await cls.issue_token(user_id)

    @classmethod
    async def revoke_token(cls, token: str) -> str | None:
        token_hash = cls._token_hash(token)
        client = await cls.get_client()
        user_id = await client.get(cls._lookup_key(token_hash))
        if not user_id:
            return None

        await client.delete(
            cls._session_key(user_id, token_hash),
            cls._lookup_key(token_hash),
        )
        await client.srem(cls._index_key(user_id), token_hash)
        return user_id

    @classmethod
    async def revoke_all_for_user(cls, user_id: str) -> int:
        client = await cls.get_client()
        token_hashes = await client.smembers(cls._index_key(user_id))
        if not token_hashes:
            await client.delete(cls._index_key(user_id))
            return 0

        keys: list[str] = [cls._index_key(user_id)]
        for token_hash in token_hashes:
            keys.append(cls._session_key(user_id, token_hash))
            keys.append(cls._lookup_key(token_hash))

        await client.delete(*keys)
        return len(token_hashes)

    @classmethod
    async def register_access_token(cls, user_id: str, jti: str, *, ttl_seconds: int) -> None:
        client = await cls.get_client()
        await client.sadd(cls._access_index_key(user_id), jti)
        await client.expire(cls._access_index_key(user_id), ttl_seconds)

    @classmethod
    async def revoke_access_token_jti(
        cls,
        jti: str,
        *,
        user_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = ttl_seconds or cls._access_ttl_seconds()
        client = await cls.get_client()
        await client.setex(cls._access_deny_key(jti), ttl, "1")
        if user_id:
            await client.srem(cls._access_index_key(user_id), jti)

    @classmethod
    async def is_access_token_jti_revoked(cls, jti: str) -> bool:
        client = await cls.get_client()
        return bool(await client.exists(cls._access_deny_key(jti)))

    @classmethod
    async def revoke_all_access_tokens_for_user(cls, user_id: str) -> int:
        client = await cls.get_client()
        jtis = await client.smembers(cls._access_index_key(user_id))
        if not jtis:
            await client.delete(cls._access_index_key(user_id))
            return 0

        ttl = cls._access_ttl_seconds()
        for jti in jtis:
            await client.setex(cls._access_deny_key(jti), ttl, "1")

        await client.delete(cls._access_index_key(user_id))
        return len(jtis)
