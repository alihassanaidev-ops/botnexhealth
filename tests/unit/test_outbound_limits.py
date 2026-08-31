"""Tests for outbound call concurrency and provider send rates (Item 18).

The whole point of this item is the failure mode in ``Watch out for``: a lost
decrement makes the in-flight count rise for ever until the clinic silently
cannot call at all. Half the tests below exist to prove that cannot happen —
that a slot whose release is never delivered still comes back on its own.

``FakeRedis`` implements the sorted-set and counter commands the Lua scripts
use, with real expiry driven by a fake clock, so a lease genuinely lapses when
time passes rather than because a mock said so.
"""

from __future__ import annotations

import logging

import pytest

from src.app.services.outbound_limits import (
    LimitDecision,
    NoOpOutboundLimiter,
    OutboundLimits,
    SendProvider,
    send_provider_for_node,
)


class FakeRedis:
    """Sorted sets, counters and TTLs, on a clock the test controls."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.counters: dict[str, int] = {}
        self.expiry_ms: dict[str, int] = {}
        self.now_ms = 1_000_000
        self.fail_with: Exception | None = None

    def advance(self, seconds: float) -> None:
        self.now_ms += int(seconds * 1000)

    def _expire_due(self) -> None:
        for key, due in list(self.expiry_ms.items()):
            if due <= self.now_ms:
                self.counters.pop(key, None)
                self.zsets.pop(key, None)
                self.expiry_ms.pop(key, None)

    # -- script contracts ---------------------------------------------
    async def eval(self, script: str, numkeys: int, *args):  # noqa: ANN001
        if self.fail_with is not None:
            raise self.fail_with
        self._expire_due()
        key = args[0]
        argv = list(args[numkeys:])

        if "ZREMRANGEBYSCORE" in script:  # acquire
            now, ceiling, ttl, token = (
                int(argv[0]), int(argv[1]), int(argv[2]), argv[3]
            )
            members = self.zsets.setdefault(key, {})
            for member, score in list(members.items()):
                if score <= now:
                    del members[member]
            if len(members) >= ceiling:
                wait = int(min(members.values()) - now) if members else 0
                return [0, len(members), max(wait, 0)]
            members[token] = now + ttl
            self.expiry_ms[key] = self.now_ms + ttl * 2
            return [1, len(members), 0]

        if "ZSCORE" in script:  # rekey
            old_token, new_token = argv[0], argv[1]
            members = self.zsets.get(key, {})
            if old_token not in members:
                return 0
            members[new_token] = members.pop(old_token)
            return 1

        # send rate
        limit, window = int(argv[0]), int(argv[1])
        count = self.counters.get(key, 0) + 1
        self.counters[key] = count
        if count == 1:
            self.expiry_ms[key] = self.now_ms + window
        if count > limit:
            ttl = self.expiry_ms.get(key, self.now_ms + window) - self.now_ms
            return [0, ttl if ttl > 0 else window]
        return [1, 0]

    # -- direct commands ----------------------------------------------
    async def zrem(self, key: str, member: str) -> int:
        if self.fail_with is not None:
            raise self.fail_with
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    async def zremrangebyscore(self, key: str, lo, hi) -> int:  # noqa: ANN001
        members = self.zsets.setdefault(key, {})
        removed = [m for m, s in members.items() if s <= float(hi)]
        for m in removed:
            del members[m]
        return len(removed)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def limits(redis: FakeRedis) -> OutboundLimits:
    return OutboundLimits(
        redis,
        call_concurrency=3,
        lease_seconds=600,
        provider_per_minute={SendProvider.TWILIO: 5, SendProvider.EMAIL: 0},
        clock=lambda: redis.now_ms,
    )


INST = "inst-1"


# ── Concurrency ceiling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calls_are_allowed_up_to_the_ceiling(limits: OutboundLimits) -> None:
    for expected in (1, 2, 3):
        decision, slot = await limits.acquire_call_slot(INST)
        assert decision.allowed is True
        assert decision.in_flight == expected
        assert slot is not None


@pytest.mark.asyncio
async def test_the_ceiling_is_enforced(limits: OutboundLimits) -> None:
    for _ in range(3):
        await limits.acquire_call_slot(INST)

    decision, slot = await limits.acquire_call_slot(INST)
    assert decision.allowed is False
    assert slot is None
    assert decision.in_flight == 3
    assert "call_concurrency_limit_3" == decision.reason
    # Held with a retry time, never dropped.
    assert decision.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_a_released_slot_is_reusable(limits: OutboundLimits) -> None:
    slots = []
    for _ in range(3):
        _, slot = await limits.acquire_call_slot(INST)
        slots.append(slot)
    assert (await limits.acquire_call_slot(INST))[0].allowed is False

    await limits.release_call_slot(slots[0])
    assert (await limits.acquire_call_slot(INST))[0].allowed is True


@pytest.mark.asyncio
async def test_a_per_clinic_ceiling_overrides_the_default(
    limits: OutboundLimits,
) -> None:
    assert (await limits.acquire_call_slot(INST, ceiling=1))[0].allowed is True
    assert (await limits.acquire_call_slot(INST, ceiling=1))[0].allowed is False


@pytest.mark.asyncio
async def test_ceilings_are_per_clinic(limits: OutboundLimits) -> None:
    for _ in range(3):
        await limits.acquire_call_slot(INST)
    assert (await limits.acquire_call_slot(INST))[0].allowed is False
    assert (await limits.acquire_call_slot("inst-2"))[0].allowed is True


# ── The failure this item exists to prevent ──────────────────────────


@pytest.mark.asyncio
async def test_a_slot_whose_release_is_never_delivered_comes_back(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    """The Watch out for: a lost decrement must not be permanent.

    Three calls time out with no result and nothing is ever released. Without
    self-expiry the clinic could never call again and nothing would say why.
    """
    for _ in range(3):
        await limits.acquire_call_slot(INST)
    assert (await limits.acquire_call_slot(INST))[0].allowed is False

    redis.advance(601)  # every lease lapses

    decision, slot = await limits.acquire_call_slot(INST)
    assert decision.allowed is True
    assert decision.in_flight == 1  # the count healed, it did not keep rising
    assert slot is not None


@pytest.mark.asyncio
async def test_a_live_call_keeps_its_slot_while_its_lease_holds(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    """The lease must outlast a legitimately long call, or it evicts itself."""
    await limits.acquire_call_slot(INST)
    redis.advance(599)
    assert (await limits.acquire_call_slot(INST))[0].in_flight == 2


@pytest.mark.asyncio
async def test_releasing_twice_is_harmless(limits: OutboundLimits) -> None:
    _, slot = await limits.acquire_call_slot(INST)
    await limits.release_call_slot(slot)
    await limits.release_call_slot(slot)
    assert await limits.in_flight_calls(INST) == 0


@pytest.mark.asyncio
async def test_releasing_an_unknown_token_is_harmless(
    limits: OutboundLimits,
) -> None:
    await limits.acquire_call_slot(INST)
    await limits.release_call_slot_by_token(INST, "never-existed")
    assert await limits.in_flight_calls(INST) == 1


# ── Re-keying to the provider's call id ──────────────────────────────


@pytest.mark.asyncio
async def test_a_slot_can_be_released_by_the_call_id(
    limits: OutboundLimits,
) -> None:
    """The outcome handler knows the call id and nothing else."""
    _, slot = await limits.acquire_call_slot(INST)
    assert await limits.rekey_call_slot(INST, slot.token, "call_abc") is True

    await limits.release_call_slot_by_token(INST, "call_abc")
    assert await limits.in_flight_calls(INST) == 0


@pytest.mark.asyncio
async def test_rekeying_does_not_change_the_count_or_the_expiry(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    _, slot = await limits.acquire_call_slot(INST)
    await limits.rekey_call_slot(INST, slot.token, "call_abc")

    assert await limits.in_flight_calls(INST) == 1
    redis.advance(601)
    assert await limits.in_flight_calls(INST) == 0  # still expires on schedule


@pytest.mark.asyncio
async def test_rekeying_a_lapsed_slot_reports_failure(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    _, slot = await limits.acquire_call_slot(INST)
    redis.advance(601)
    await limits.acquire_call_slot(INST)  # prunes the expired lease
    assert await limits.rekey_call_slot(INST, slot.token, "call_abc") is False


# ── Provider send rates ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sends_are_allowed_up_to_the_rate(limits: OutboundLimits) -> None:
    for _ in range(5):
        assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-1")).allowed


@pytest.mark.asyncio
async def test_the_send_rate_is_enforced_and_reports_a_retry_time(
    limits: OutboundLimits,
) -> None:
    for _ in range(5):
        await limits.check_send_rate(SendProvider.TWILIO, "loc-1")

    decision = await limits.check_send_rate(SendProvider.TWILIO, "loc-1")
    assert decision.allowed is False
    assert decision.retry_after_seconds > 0
    assert "twilio_send_rate_5_per_minute" == decision.reason


@pytest.mark.asyncio
async def test_the_rate_window_rolls_over(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    for _ in range(5):
        await limits.check_send_rate(SendProvider.TWILIO, "loc-1")
    assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-1")).allowed is False

    redis.advance(61)
    assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-1")).allowed is True


@pytest.mark.asyncio
async def test_send_rates_are_scoped_per_location(limits: OutboundLimits) -> None:
    """Credentials are per location, so one location's burst is its own."""
    for _ in range(5):
        await limits.check_send_rate(SendProvider.TWILIO, "loc-1")
    assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-1")).allowed is False
    assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-2")).allowed is True


@pytest.mark.asyncio
async def test_a_zero_rate_disables_the_limit(limits: OutboundLimits) -> None:
    """Zero must mean "no limit", not "block every message"."""
    for _ in range(50):
        assert (await limits.check_send_rate(SendProvider.EMAIL, "loc-1")).allowed


@pytest.mark.asyncio
async def test_an_unmetered_provider_is_allowed(limits: OutboundLimits) -> None:
    assert (await limits.check_send_rate("carrier-pigeon", "loc-1")).allowed is True


# ── Fail open ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_down_allows_the_call(
    limits: OutboundLimits, redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    redis.fail_with = ConnectionError("redis is gone")
    with caplog.at_level(logging.WARNING):
        decision, slot = await limits.acquire_call_slot(INST)

    assert decision.allowed is True
    assert slot is None  # nothing to release; the caller must cope with None
    assert any("call limiter unavailable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_redis_down_allows_the_send(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    redis.fail_with = ConnectionError("redis is gone")
    assert (await limits.check_send_rate(SendProvider.TWILIO, "loc-1")).allowed is True


@pytest.mark.asyncio
async def test_a_full_clinic_fails_open_if_redis_dies(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    """Losing the store must release traffic, never strand it."""
    for _ in range(3):
        await limits.acquire_call_slot(INST)
    assert (await limits.acquire_call_slot(INST))[0].allowed is False

    redis.fail_with = ConnectionError("redis is gone")
    assert (await limits.acquire_call_slot(INST))[0].allowed is True


@pytest.mark.asyncio
async def test_redis_down_does_not_raise_on_release(
    limits: OutboundLimits, redis: FakeRedis
) -> None:
    _, slot = await limits.acquire_call_slot(INST)
    redis.fail_with = ConnectionError("redis is gone")
    await limits.release_call_slot(slot)  # must not raise into the outcome path


# ── Wiring helpers ───────────────────────────────────────────────────


def test_metered_node_types_map_to_their_provider() -> None:
    assert send_provider_for_node("send_sms") is SendProvider.TWILIO
    assert send_provider_for_node("send_email") is SendProvider.EMAIL


def test_voice_is_not_send_rate_limited() -> None:
    """Voice is bounded by the concurrency ceiling; two limits would double-hold."""
    assert send_provider_for_node("send_voice") is None


@pytest.mark.asyncio
async def test_noop_limiter_never_holds_anything() -> None:
    noop = NoOpOutboundLimiter()
    for _ in range(100):
        decision, _ = await noop.acquire_call_slot(INST)
        assert decision.allowed is True
    assert await noop.check_send_rate(SendProvider.TWILIO, "loc-1") == LimitDecision(
        True
    )
