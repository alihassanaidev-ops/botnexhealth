import pytest

from src.app.config import build_database_url, normalize_redis_url, settings
from src.app.services.refresh_token_service import (
    RefreshTokenReplayError,
    RefreshTokenService,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.sets:
                del self.sets[key]
                deleted += 1
        return deleted

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        removed = 0
        for member in members:
            if member in bucket:
                bucket.remove(member)
                removed += 1
        return removed

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def patch_refresh_token_client(fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch):
    async def _get_client(_cls) -> FakeRedis:
        return fake_redis

    monkeypatch.setattr(
        RefreshTokenService,
        "get_client",
        classmethod(_get_client),
    )


@pytest.mark.asyncio
async def test_issue_token_and_lookup_user(fake_redis: FakeRedis) -> None:
    token = await RefreshTokenService.issue_token("user-1")

    user_id = await RefreshTokenService.get_user_id_for_token(token)

    assert token
    assert user_id == "user-1"


@pytest.mark.asyncio
async def test_rotate_token_invalidates_old_token(fake_redis: FakeRedis) -> None:
    token = await RefreshTokenService.issue_token("user-2")

    rotated = await RefreshTokenService.rotate_token("user-2", token)

    assert rotated
    # Presenting the rotated token must trip replay detection — the legitimate
    # client never replays a rotated token, so this signals theft. The service
    # revokes everything and raises so the caller can audit + 401.
    with pytest.raises(RefreshTokenReplayError):
        await RefreshTokenService.get_user_id_for_token(token)
    # The new token is invalid too because replay detection revoked the user.
    assert await RefreshTokenService.get_user_id_for_token(rotated) is None


@pytest.mark.asyncio
async def test_revoke_all_for_user_clears_all_tokens(fake_redis: FakeRedis) -> None:
    token_a = await RefreshTokenService.issue_token("user-3")
    token_b = await RefreshTokenService.issue_token("user-3")

    revoked = await RefreshTokenService.revoke_all_for_user("user-3")

    assert revoked == 2
    assert await RefreshTokenService.get_user_id_for_token(token_a) is None
    assert await RefreshTokenService.get_user_id_for_token(token_b) is None


@pytest.mark.asyncio
async def test_revoke_access_token_jti_marks_token_as_revoked(fake_redis: FakeRedis) -> None:
    await RefreshTokenService.register_access_token("user-4", "jti-1", ttl_seconds=900)

    await RefreshTokenService.revoke_access_token_jti(
        "jti-1",
        user_id="user-4",
        ttl_seconds=900,
    )

    assert await RefreshTokenService.is_access_token_jti_revoked("jti-1") is True
    assert "jti-1" not in fake_redis.sets.get("access_index:user-4", set())


@pytest.mark.asyncio
async def test_revoke_all_access_tokens_for_user_marks_all_jtis(fake_redis: FakeRedis) -> None:
    await RefreshTokenService.register_access_token("user-5", "jti-a", ttl_seconds=900)
    await RefreshTokenService.register_access_token("user-5", "jti-b", ttl_seconds=900)

    revoked = await RefreshTokenService.revoke_all_access_tokens_for_user("user-5")

    assert revoked == 2
    assert await RefreshTokenService.is_access_token_jti_revoked("jti-a") is True
    assert await RefreshTokenService.is_access_token_jti_revoked("jti-b") is True
    assert "access_index:user-5" not in fake_redis.sets


def test_normalize_redis_url_adds_ssl_cert_reqs_for_tls() -> None:
    assert (
        normalize_redis_url("rediss://cache.example:6379/0")
        == "rediss://cache.example:6379/0?ssl_cert_reqs=required"
    )


def test_build_database_url_from_discrete_settings() -> None:
    assert build_database_url(
        username="app_user",
        password="p@ss word",
        host="db.internal",
        port=5432,
        database_name="nexhealth",
    ) == (
        "postgresql+asyncpg://app_user:p%40ss+word@db.internal:5432/nexhealth"
    )


def test_refresh_token_service_uses_normalized_tls_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "redis_url", "rediss://cache.example:6379/0")
    monkeypatch.setattr(settings, "celery_broker_url", None)

    assert (
        RefreshTokenService._redis_url()
        == "rediss://cache.example:6379/0?ssl_cert_reqs=required"
    )
