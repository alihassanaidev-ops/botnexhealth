from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app import dependencies
from src.app.nexhealth.rate_limit import NexHealthRateLimiter
from src.app.nexhealth.token_manager import RedisTokenCache
from src.app.pms.nexhealth.adapter import NexHealthAdapter


def test_resolve_nexhealth_credential_prefers_institution_key(monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "nexhealth_api_key", "platform-key")
    institution = SimpleNamespace(
        id="inst-1",
        nexhealth_api_key_encrypted="encrypted",
        nexhealth_credential_mode="institution",
        nexhealth_api_key="clinic-key",
    )

    credential = dependencies.resolve_nexhealth_credential(institution)

    assert credential.mode == "institution"
    assert credential.api_key == "clinic-key"
    assert credential.api_key_hash == NexHealthRateLimiter.hash_api_key("clinic-key")


def test_resolve_nexhealth_credential_uses_platform_key_in_platform_mode(monkeypatch) -> None:
    """Renamed: this is no longer a fallback. Platform mode is a declared choice,
    and an institution set to use its own key now fails rather than falling back
    — see test_nexhealth_credential_mode.py."""
    monkeypatch.setattr(dependencies.settings, "nexhealth_api_key", "platform-key")
    institution = SimpleNamespace(
        id="inst-1",
        nexhealth_api_key_encrypted=None,
        nexhealth_credential_mode="platform",
        nexhealth_api_key=None,
    )

    credential = dependencies.resolve_nexhealth_credential(institution)

    assert credential.mode == "platform"
    assert credential.api_key == "platform-key"
    assert credential.api_key_hash == NexHealthRateLimiter.hash_api_key("platform-key")


@pytest.mark.asyncio
async def test_redis_token_cache_can_be_keyed_per_api_key() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    key_hash = NexHealthRateLimiter.hash_api_key("clinic-key")
    cache = RedisTokenCache(redis, key=f"nh:token:{key_hash}")
    await cache.set("token", expires_in=3600)

    args, kwargs = redis.set.call_args
    assert args[0] == f"nh:token:{key_hash}"
    assert args[1] == "token"
    assert kwargs["ex"] == 3300


@pytest.mark.asyncio
async def test_nexhealth_adapter_create_uses_institution_credential(monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "nexhealth_api_key", "platform-key")

    captured = {}

    async def fake_client_for_credential(credential):
        captured["credential"] = credential
        return SimpleNamespace()

    monkeypatch.setattr(
        dependencies,
        "get_nexhealth_client_for_credential",
        fake_client_for_credential,
    )

    institution = SimpleNamespace(
        id="inst-1",
        slug="clinic",
        nexhealth_api_key_encrypted="encrypted",
        nexhealth_credential_mode="institution",
        nexhealth_api_key="clinic-key",
    )
    location = SimpleNamespace(
        slug="main",
        nexhealth_subdomain="clinic-sub",
        nexhealth_location_id="loc-1",
    )

    adapter = await NexHealthAdapter.create(institution, location)

    assert captured["credential"].mode == "institution"
    assert captured["credential"].api_key == "clinic-key"
    assert adapter.credential_mode == "institution"
    assert adapter.api_key_hash == NexHealthRateLimiter.hash_api_key("clinic-key")
