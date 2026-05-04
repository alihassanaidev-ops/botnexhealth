"""Tests for the NexHealth distributed rate limiter.

Cover the three things that matter:
  1. Endpoint classification matches NexHealth's documented buckets.
  2. The Lua script's contract — when Redis says "0", we admit; when it
     says "N>0", we sleep for N ms (plus jitter) and retry.
  3. Fail-open behaviour when Redis is unreachable — the limiter logs a
     warning and admits the request rather than blocking the integration.

We mock the Redis ``eval`` call rather than running a real Redis: the
Lua script itself is exercised in integration testing against the real
deploy. These tests pin the *Python* contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.nexhealth.rate_limit import (
    NexHealthLocalRateLimitExceeded,
    NexHealthRateLimiter,
    classify_endpoint,
)


# ── Endpoint classifier ──────────────────────────────────────────────


def test_classify_get_appointments_uses_slow_read_per_second() -> None:
    """GET /appointments and /appointment_slots get the documented 10/s sub-cap."""
    policy = classify_endpoint("GET", "/appointments")
    assert policy.class_name == "appts_read"
    assert policy.class_per_s == 10
    assert policy.class_per_min == 1000

    slots = classify_endpoint("GET", "/appointment_slots")
    assert slots.class_name == "appts_read"
    assert slots.class_per_s == 10


def test_classify_post_appointments_uses_general_per_second() -> None:
    """Non-GET appointment writes share the 100/s + 1000/min budget."""
    policy = classify_endpoint("POST", "/appointments")
    assert policy.class_name == "patients_appts"
    assert policy.class_per_s == 100
    assert policy.class_per_min == 1000


def test_classify_patients_endpoint_is_patients_appts_class() -> None:
    """All patient endpoints land in the 1000/min bucket regardless of method."""
    for method in ("GET", "POST", "PATCH", "DELETE"):
        policy = classify_endpoint(method, "/patients/123")
        assert policy.class_name == "patients_appts"
        assert policy.class_per_min == 1000


def test_classify_unknown_endpoint_uses_other_class_with_2000_per_min() -> None:
    policy = classify_endpoint("GET", "/providers")
    assert policy.class_name == "other"
    assert policy.class_per_min == 2000


def test_classify_global_per_s_is_always_100() -> None:
    """Global per-second cap is shared across every endpoint."""
    for method, path in (
        ("GET", "/appointments"),
        ("POST", "/patients"),
        ("GET", "/providers"),
    ):
        assert classify_endpoint(method, path).global_per_s == 100


# ── Limiter — happy path ─────────────────────────────────────────────


class _FakeRedis:
    """Minimal stand-in for ``redis.asyncio.Redis``.

    Records every ``eval`` call and replays a canned sequence of return
    values so tests can simulate "blocked then allowed" without timing.
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, _script: str, _numkeys: int, *args: Any) -> Any:
        self.calls.append(args)
        if not self._responses:
            return 0
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_acquire_passes_through_when_redis_says_zero() -> None:
    redis = _FakeRedis([0])
    limiter = NexHealthRateLimiter(redis, max_wait_ms=1000)

    await limiter.acquire("tenant-abc", "GET", "/providers")

    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_acquire_keys_separate_per_tenant_and_class() -> None:
    """A key collision across tenants would silently share their budget —
    every Redis key MUST embed the api_key_id and the endpoint class."""
    redis = _FakeRedis([0, 0])
    limiter = NexHealthRateLimiter(redis, max_wait_ms=1000)

    await limiter.acquire("tenant-A", "GET", "/appointments")
    await limiter.acquire("tenant-B", "POST", "/patients")

    keys_call_one = redis.calls[0][:3]
    keys_call_two = redis.calls[1][:3]

    # Tenant A shows up with the appts_read class; tenant B with patients_appts.
    assert all("tenant-A" in k for k in keys_call_one)
    assert all("appts_read" in k for k in keys_call_one[1:])
    assert all("tenant-B" in k for k in keys_call_two)
    assert all("patients_appts" in k for k in keys_call_two[1:])
    assert keys_call_one[0] != keys_call_two[0], "Global keys must be tenant-scoped"


@pytest.mark.asyncio
async def test_acquire_retries_after_redis_says_wait(monkeypatch) -> None:
    """When Redis returns a positive ms value, we sleep that long (plus
    jitter) and re-try, eventually getting through when it returns 0."""
    redis = _FakeRedis([50, 0])  # blocked once, then allowed
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("src.app.nexhealth.rate_limit.asyncio.sleep", fake_sleep)

    limiter = NexHealthRateLimiter(redis, max_wait_ms=1000)
    await limiter.acquire("tenant-A", "GET", "/providers")

    assert len(sleeps) == 1
    # 50ms minimum wait + jitter (10–80ms inclusive)
    assert 0.06 <= sleeps[0] <= 0.13


@pytest.mark.asyncio
async def test_acquire_raises_when_wait_would_exceed_deadline(monkeypatch) -> None:
    """If the next wait would push us past max_wait_ms, raise instead of
    sleeping. Callers should treat this as 503-equivalent."""
    redis = _FakeRedis([800])  # 800ms wait; deadline is 100ms

    async def fake_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr("src.app.nexhealth.rate_limit.asyncio.sleep", fake_sleep)
    limiter = NexHealthRateLimiter(redis, max_wait_ms=100)

    with pytest.raises(NexHealthLocalRateLimitExceeded) as exc:
        await limiter.acquire("tenant-A", "GET", "/providers")

    assert exc.value.api_key_id == "tenant-A"
    assert exc.value.class_name == "other"


# ── Limiter — fail-open ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acquire_is_fail_open_when_redis_errors(caplog) -> None:
    """A Redis outage MUST NOT block NexHealth traffic. The limiter logs
    a warning and admits the request; the reactive 429 handler is the
    safety net."""
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))
    limiter = NexHealthRateLimiter(redis, max_wait_ms=1000)

    with caplog.at_level("WARNING", logger="src.app.nexhealth.rate_limit"):
        # Should NOT raise.
        await limiter.acquire("tenant-A", "GET", "/providers")

    redis.eval.assert_awaited_once()
    assert any(
        "rate limiter unreachable" in rec.getMessage().lower()
        for rec in caplog.records
    )


# ── API key hashing ─────────────────────────────────────────────────


def test_hash_api_key_is_stable_and_does_not_leak_input() -> None:
    """Hashing must be deterministic (so all tasks key the same buckets)
    and short enough for clean Redis keys, but never the raw secret."""
    raw = "nexhealth-prod-api-key-don't-leak-this"
    hashed = NexHealthRateLimiter.hash_api_key(raw)

    assert hashed == NexHealthRateLimiter.hash_api_key(raw)
    assert raw not in hashed
    assert len(hashed) == 16
    assert all(c in "0123456789abcdef" for c in hashed)
