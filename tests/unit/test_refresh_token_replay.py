"""Refresh-token replay detection.

When a previously rotated refresh token is presented again, every session
for the user is revoked and a RefreshTokenReplayError is raised — the
standard signal for refresh-token theft.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.refresh_token_service import (
    RefreshTokenReplayError,
    RefreshTokenService,
)


class _FakeRedis:
    """Tiny stand-in: tracks setex/exists/delete/sadd/srem/smembers/expire."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.kv[key] = value

    async def exists(self, key: str) -> int:
        if key in self.kv:
            return 1
        if key in self.sets and self.sets[key]:
            return 1
        return 0

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if self.kv.pop(k, None) is not None:
                count += 1
            if self.sets.pop(k, None) is not None:
                count += 1
        return count

    async def sadd(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def srem(self, key: str, *values: str) -> int:
        bucket = self.sets.get(key, set())
        before = len(bucket)
        bucket.difference_update(values)
        if not bucket:
            self.sets.pop(key, None)
        return before - len(bucket)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    client = _FakeRedis()
    monkeypatch.setattr(
        RefreshTokenService,
        "get_client",
        AsyncMock(return_value=client),
    )
    return client


@pytest.mark.asyncio
async def test_rotation_records_old_hash_and_issues_new_token(fake_redis: _FakeRedis):
    user_id = "user-1"
    token = await RefreshTokenService.issue_token(user_id)

    new_token = await RefreshTokenService.rotate_token(user_id, token)

    assert new_token is not None
    assert new_token != token
    # The old token's hash must now be in the rotated-set so a replay trips.
    old_hash_key = RefreshTokenService._rotated_key(
        RefreshTokenService._token_hash(token)
    )
    assert await fake_redis.exists(old_hash_key) == 1


@pytest.mark.asyncio
async def test_replay_of_old_token_raises_and_revokes_all_sessions(fake_redis: _FakeRedis):
    user_id = "user-1"
    first_token = await RefreshTokenService.issue_token(user_id)
    new_token = await RefreshTokenService.rotate_token(user_id, first_token)
    assert new_token is not None
    # User had a current session — confirm before replay.
    assert await RefreshTokenService.get_user_id_for_token(new_token) == user_id

    with pytest.raises(RefreshTokenReplayError):
        await RefreshTokenService.rotate_token(user_id, first_token)

    # All sessions should be wiped after the replay signal.
    assert await RefreshTokenService.get_user_id_for_token(new_token) is None


@pytest.mark.asyncio
async def test_rotate_returns_none_for_unknown_token(fake_redis: _FakeRedis):
    result = await RefreshTokenService.rotate_token("user-1", "never-issued")
    assert result is None
