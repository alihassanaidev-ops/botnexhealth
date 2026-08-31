"""Tests for the shared service circuit breaker (Item 17).

The state machine lives in Lua so that racing workers cannot disagree, which
means these tests need Redis semantics rather than a mock that returns whatever
we tell it. ``FakeRedis`` below implements the handful of commands the scripts
use — with real TTL behaviour driven by a fake clock — and an ``eval`` that runs
the scripts' *contract*, not the Lua text itself. That keeps the Python side
honest about what it does with each reply while the Lua is exercised for real
against Redis in staging.

What matters here:
  1. A breaker opens only after the threshold is crossed, and only on failures
     the caller says are the service's fault.
  2. An open breaker refuses work and reports how long to hold it for.
  3. Recovery: cooldown → one probe → success closes, failure re-opens.
  4. Only one caller gets the probe.
  5. Redis being down allows traffic through, with a warning.
"""

from __future__ import annotations

import logging

import pytest

from src.app.services.circuit_breaker import (
    BreakerService,
    BreakerState,
    CircuitBreaker,
    NoOpCircuitBreaker,
    breaker_service_for_node,
)


# ── A Redis stand-in with real expiry semantics ──────────────────────


class FakeRedis:
    """Enough of Redis for the breaker scripts, with a controllable clock."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry_ms: dict[str, int] = {}
        self.now_ms = 1_000_000
        self.fail_with: Exception | None = None

    # -- helpers -------------------------------------------------------
    def advance(self, seconds: float) -> None:
        self.now_ms += int(seconds * 1000)

    def _expire_due(self) -> None:
        for key, due in list(self.expiry_ms.items()):
            if due <= self.now_ms:
                self.store.pop(key, None)
                self.expiry_ms.pop(key, None)

    def _exists(self, key: str) -> int:
        self._expire_due()
        return 1 if key in self.store else 0

    def _pttl(self, key: str) -> int:
        self._expire_due()
        if key not in self.store:
            return -2
        if key not in self.expiry_ms:
            return -1
        return self.expiry_ms[key] - self.now_ms

    def _set(self, key: str, value: str, *, px: int, nx: bool = False) -> bool:
        self._expire_due()
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry_ms[key] = self.now_ms + px
        return True

    def _incr(self, key: str) -> int:
        self._expire_due()
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def _delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)
            self.expiry_ms.pop(key, None)

    # -- the script contracts -----------------------------------------
    async def eval(self, script: str, numkeys: int, *args):  # noqa: ANN001
        if self.fail_with is not None:
            raise self.fail_with
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        open_k, half_k, probe_k, fails_k = (keys + [None] * 4)[:4]

        if "return {1, 'closed', 0}" in script:  # ALLOW
            probe_ttl = int(argv[0])
            if self._exists(open_k):
                return [0, "open", max(self._pttl(open_k), 0)]
            if self._exists(half_k):
                if self._set(probe_k, "1", px=probe_ttl, nx=True):
                    return [1, "half_open", 0]
                ttl = self._pttl(probe_k)
                return [0, "half_open", probe_ttl if ttl < 0 else ttl]
            return [1, "closed", 0]

        if "local function trip()" in script:  # FAILURE
            threshold, window_ms, cooldown_ms, half_ms = (int(a) for a in argv[:4])

            def trip() -> None:
                self._set(open_k, "1", px=cooldown_ms)
                self._set(half_k, "1", px=cooldown_ms + half_ms)
                self._delete(probe_k, fails_k)

            if self._exists(open_k):
                return ["open", 0]
            if self._exists(half_k):
                trip()
                return ["open", 1]
            count = self._incr(fails_k)
            if count == 1:
                self.expiry_ms[fails_k] = self.now_ms + window_ms
            if count >= threshold:
                trip()
                return ["open", 1]
            return ["closed", 0]

        # SUCCESS
        recovered = 1 if (self._exists(open_k) or self._exists(half_k)) else 0
        self._delete(open_k, half_k, probe_k, fails_k)
        return ["closed", recovered]


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def breaker(redis: FakeRedis) -> CircuitBreaker:
    return CircuitBreaker(
        redis,
        failure_threshold=3,
        failure_window_seconds=60,
        cooldown_seconds=30,
        half_open_seconds=300,
        probe_timeout_seconds=60,
    )


SERVICE = BreakerService.TWILIO
SCOPE = "loc-1"


# ── Closed → open ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allows_calls_while_closed(breaker: CircuitBreaker) -> None:
    decision = await breaker.allow(SERVICE, SCOPE)
    assert decision.allowed is True
    assert decision.state is BreakerState.CLOSED
    assert decision.retry_after_seconds == 0


@pytest.mark.asyncio
async def test_stays_closed_below_the_threshold(breaker: CircuitBreaker) -> None:
    """Two failures out of three is a blip, not an outage."""
    for _ in range(2):
        assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.CLOSED
    assert (await breaker.allow(SERVICE, SCOPE)).allowed is True


@pytest.mark.asyncio
async def test_opens_on_the_threshold_failure(breaker: CircuitBreaker) -> None:
    for _ in range(2):
        await breaker.record_failure(SERVICE, SCOPE)
    assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.OPEN

    decision = await breaker.allow(SERVICE, SCOPE)
    assert decision.allowed is False
    assert decision.state is BreakerState.OPEN
    # Held for the cooldown, so the caller can schedule a timer rather than fail.
    assert decision.retry_after_seconds == 30


@pytest.mark.asyncio
async def test_a_success_clears_accumulated_failures(
    breaker: CircuitBreaker,
) -> None:
    """An intermittent failure must not accumulate into an outage over hours."""
    await breaker.record_failure(SERVICE, SCOPE)
    await breaker.record_failure(SERVICE, SCOPE)
    await breaker.record_success(SERVICE, SCOPE)

    # The counter reset, so two more failures still leave it closed.
    await breaker.record_failure(SERVICE, SCOPE)
    assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.CLOSED
    assert (await breaker.allow(SERVICE, SCOPE)).allowed is True


@pytest.mark.asyncio
async def test_failures_expire_with_their_window(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    """Failures spread thinly over hours are not an outage."""
    await breaker.record_failure(SERVICE, SCOPE)
    await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(61)  # past the 60s window
    assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_breakers_are_isolated_per_service_and_per_clinic(
    breaker: CircuitBreaker,
) -> None:
    """One clinic's bad credentials must not stop everybody else."""
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)

    assert (await breaker.allow(SERVICE, SCOPE)).allowed is False
    assert (await breaker.allow(SERVICE, "loc-2")).allowed is True
    assert (await breaker.allow(BreakerService.RETELL, SCOPE)).allowed is True


# ── Recovery ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_is_admitted_once_the_cooldown_expires(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)  # cooldown elapsed

    decision = await breaker.allow(SERVICE, SCOPE)
    assert decision.allowed is True
    assert decision.state is BreakerState.HALF_OPEN


@pytest.mark.asyncio
async def test_only_one_caller_gets_the_probe(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    """Otherwise every waiting worker stampedes the recovering service."""
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)

    first = await breaker.allow(SERVICE, SCOPE)
    second = await breaker.allow(SERVICE, SCOPE)

    assert first.allowed is True
    assert second.allowed is False
    assert second.state is BreakerState.HALF_OPEN
    assert second.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_successful_probe_closes_the_breaker(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    """Recovery is automatic — no restart, no operator action."""
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)
    await breaker.allow(SERVICE, SCOPE)  # take the probe

    assert await breaker.record_success(SERVICE, SCOPE) is BreakerState.CLOSED

    decision = await breaker.allow(SERVICE, SCOPE)
    assert decision.allowed is True
    assert decision.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_failed_probe_reopens_for_a_fresh_cooldown(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)
    await breaker.allow(SERVICE, SCOPE)  # take the probe

    assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.OPEN

    decision = await breaker.allow(SERVICE, SCOPE)
    assert decision.allowed is False
    assert decision.state is BreakerState.OPEN
    assert decision.retry_after_seconds == 30


@pytest.mark.asyncio
async def test_abandoned_probe_expires_so_the_breaker_cannot_wedge(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    """A worker that dies holding the probe must not block recovery forever."""
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)
    await breaker.allow(SERVICE, SCOPE)  # taken, and never reported back

    assert (await breaker.allow(SERVICE, SCOPE)).allowed is False
    redis.advance(61)  # probe timeout elapsed
    assert (await breaker.allow(SERVICE, SCOPE)).allowed is True


# ── Alerts ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opening_logs_one_alert_not_one_per_call(
    breaker: CircuitBreaker, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            await breaker.record_failure(SERVICE, SCOPE)
        await breaker.record_failure(SERVICE, SCOPE)  # straggler, already open

    opened = [r for r in caplog.records if "circuit breaker OPENED" in r.message]
    assert len(opened) == 1


@pytest.mark.asyncio
async def test_recovery_logs_an_alert(
    breaker: CircuitBreaker, redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    redis.advance(31)
    await breaker.allow(SERVICE, SCOPE)

    with caplog.at_level(logging.ERROR):
        await breaker.record_success(SERVICE, SCOPE)

    assert any("circuit breaker CLOSED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_success_on_a_healthy_breaker_is_silent(
    breaker: CircuitBreaker, caplog: pytest.LogCaptureFixture
) -> None:
    """Every successful send calls this; it must not log an alert each time."""
    with caplog.at_level(logging.ERROR):
        await breaker.record_success(SERVICE, SCOPE)
    assert not [r for r in caplog.records if "circuit breaker" in r.message]


# ── Fail-open ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_down_allows_the_call_with_a_warning(
    breaker: CircuitBreaker, redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    """An outage in the safety mechanism must not become a total outage."""
    redis.fail_with = ConnectionError("redis is gone")

    with caplog.at_level(logging.WARNING):
        decision = await breaker.allow(SERVICE, SCOPE)

    assert decision.allowed is True
    assert decision.state is BreakerState.UNAVAILABLE
    assert any("circuit breaker unavailable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_redis_down_does_not_break_recording(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    redis.fail_with = ConnectionError("redis is gone")
    assert await breaker.record_failure(SERVICE, SCOPE) is BreakerState.UNAVAILABLE
    assert await breaker.record_success(SERVICE, SCOPE) is BreakerState.UNAVAILABLE


@pytest.mark.asyncio
async def test_an_open_breaker_fails_open_if_redis_dies(
    breaker: CircuitBreaker, redis: FakeRedis
) -> None:
    """Losing the store must release traffic, never strand it."""
    for _ in range(3):
        await breaker.record_failure(SERVICE, SCOPE)
    assert (await breaker.allow(SERVICE, SCOPE)).allowed is False

    redis.fail_with = ConnectionError("redis is gone")
    assert (await breaker.allow(SERVICE, SCOPE)).allowed is True


# ── Wiring helpers ───────────────────────────────────────────────────


def test_node_types_map_to_their_provider() -> None:
    assert breaker_service_for_node("send_sms") is BreakerService.TWILIO
    assert breaker_service_for_node("send_email") is BreakerService.EMAIL
    assert breaker_service_for_node("send_voice") is BreakerService.RETELL


def test_non_send_nodes_have_no_breaker() -> None:
    """A wait or a branch calls nobody, so it is never held by a breaker."""
    assert breaker_service_for_node("wait") is None
    assert breaker_service_for_node("update_patient_status") is None


@pytest.mark.asyncio
async def test_noop_breaker_always_allows() -> None:
    noop = NoOpCircuitBreaker()
    for _ in range(50):
        await noop.record_failure(SERVICE, SCOPE)
    decision = await noop.allow(SERVICE, SCOPE)
    assert decision.allowed is True
    assert decision.state is BreakerState.CLOSED
