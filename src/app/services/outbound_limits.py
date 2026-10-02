"""Outbound volume limits: call concurrency and provider send rates (Item 18).

Two separate limits, both shared across every running copy of the app, both
holding work rather than dropping it:

  - **Concurrency.** A ceiling on how many outbound calls one clinic may have in
    progress at once. Without it a clinic launching a five-hundred-patient
    recall dials as fast as workers allow: the voice provider is overwhelmed,
    capacity other clinics need is exhausted, and the practice gets a wave of
    call-backs nobody can answer.

  - **Send rate.** A per-provider ceiling on messages per unit time. Providers
    enforce their own rates; exceeding them gets messages rejected or the
    account throttled, and a Twilio rejection is exactly the failure the retry
    work in Item 14 exists to surface.

``nexhealth/rate_limit.py`` is the model for both — Lua for atomicity, fixed
windows, fail open when Redis is unreachable.


Why concurrency uses leases rather than a counter
-------------------------------------------------

The obvious implementation is INCR when a call starts and DECR when it ends.
It is also the one thing this item must not do. A call that times out with no
result, a worker killed mid-flight, a webhook that never arrives — each one
loses a decrement, and a lost decrement is permanent. The count only ever
rises, until the clinic silently cannot place calls at all and nothing in the
logs explains why. It fails closed, quietly, and looks like the feature working.

So a slot is a **lease with an expiry**, held in a sorted set scored by the
instant it lapses. Acquiring prunes everything already expired, which makes the
structure self-healing: if every release in the system were lost, every ceiling
would still be correct one lease-TTL later. Releasing promptly is an
optimisation, not a correctness requirement.

The TTL is therefore sized to the longest a call can legitimately be
outstanding — the voice node parks for ``VoiceParked.timeout_minutes`` (30) —
plus margin, so a live call is never evicted from its own slot.


Scopes, and why the two limits use different ones
-------------------------------------------------

Concurrency is scoped to the **institution**: the constraint it models is the
practice's own capacity to handle call-backs, and the voice provider's capacity
for that customer, neither of which is per location.

Send rate is scoped to the **location**, because provider credentials are —
Twilio sub-accounts and sender numbers are resolved per location, so that is
the boundary the provider actually meters. This matches the circuit breaker's
scope for the same reason.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from redis.asyncio import from_url

from src.app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "SendProvider",
    "LimitDecision",
    "ConcurrencySlot",
    "OutboundLimiter",
    "NoOpOutboundLimiter",
    "OutboundLimits",
    "send_provider_for_node",
]


class SendProvider(str, Enum):
    """Providers with their own metered sending rate."""

    TWILIO = "twilio"
    EMAIL = "email"


_NODE_TYPE_PROVIDERS: dict[str, SendProvider] = {
    "send_sms": SendProvider.TWILIO,
    "send_email": SendProvider.EMAIL,
}


def send_provider_for_node(node_type: str) -> SendProvider | None:
    """The metered provider a send node uses, or None if it is not metered.

    ``send_voice`` is absent deliberately: calls are bounded by the concurrency
    ceiling, which is the meaningful limit for voice. Counting dial attempts per
    minute as well would hold work twice for one constraint.
    """
    return _NODE_TYPE_PROVIDERS.get(node_type)


@dataclass(frozen=True)
class LimitDecision:
    """Whether work may proceed, and how long to hold it if not."""

    allowed: bool
    #: How long to defer for. Zero when allowed.
    retry_after_seconds: int = 0
    #: Why it was held, for the dispatcher's log line. None when allowed.
    reason: str | None = None
    #: Slots in use at the moment of the check, for logs. None for rate limits.
    in_flight: int | None = None


@dataclass(frozen=True)
class ConcurrencySlot:
    """A held call slot. Hand it back to ``release`` when the call ends."""

    institution_id: str
    token: str


# Defaults. The concurrency ceiling is the number the doc calls configurable;
# it is deliberately generous, since the failure it prevents is a wave of
# hundreds, not a busy afternoon.
_DEFAULT_CALL_CONCURRENCY = 20
# Twilio's default long-code throughput is 1 message/second; short codes and
# messaging services are higher. 60/minute is that floor expressed per window.
_DEFAULT_TWILIO_PER_MINUTE = 60
_DEFAULT_EMAIL_PER_MINUTE = 300
# Comfortably past the 30-minute voice parking timeout, so a live call is never
# evicted from its own slot, while a lost release still heals within the hour.
_DEFAULT_LEASE_SECONDS = 2400


@runtime_checkable
class OutboundLimiter(Protocol):
    """Contract the dispatcher and voice executor depend on."""

    async def acquire_call_slot(
        self, institution_id: str, *, ceiling: int | None = None
    ) -> tuple[LimitDecision, ConcurrencySlot | None]: ...

    async def release_call_slot(self, slot: ConcurrencySlot) -> None: ...

    async def release_call_slot_by_token(
        self, institution_id: str, token: str
    ) -> None: ...

    async def rekey_call_slot(
        self, institution_id: str, old_token: str, new_token: str
    ) -> bool: ...

    async def check_send_rate(
        self, provider: SendProvider | str, scope_id: str
    ) -> LimitDecision: ...


class NoOpOutboundLimiter:
    """Default stub — no ceiling, no rate. Mirrors ``NoOpComplianceGate``."""

    async def acquire_call_slot(
        self, institution_id: str, *, ceiling: int | None = None
    ) -> tuple[LimitDecision, ConcurrencySlot | None]:
        return LimitDecision(True), None

    async def release_call_slot(self, slot: ConcurrencySlot) -> None:
        return None

    async def release_call_slot_by_token(
        self, institution_id: str, token: str
    ) -> None:
        return None

    async def rekey_call_slot(
        self, institution_id: str, old_token: str, new_token: str
    ) -> bool:
        return False

    async def check_send_rate(
        self, provider: SendProvider | str, scope_id: str
    ) -> LimitDecision:
        return LimitDecision(True)


# ── Lua ─────────────────────────────────────────────────────────────────

# Acquire a call slot.
#   KEYS[1] the clinic's lease set
#   ARGV[1] now_ms  ARGV[2] ceiling  ARGV[3] lease_ttl_ms  ARGV[4] token
#
# Pruning expired leases on every acquire is what makes a lost release
# self-correcting rather than permanent.
#
# Returns {granted, in_flight, wait_ms}.
_ACQUIRE_SLOT_LUA = """
local now = tonumber(ARGV[1])
local ceiling = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local in_flight = redis.call('ZCARD', KEYS[1])

if in_flight >= ceiling then
    -- The earliest expiry is the soonest a slot can free up on its own. A
    -- call ending normally frees one sooner; this is the guaranteed bound.
    local soonest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local wait = 0
    if soonest[2] then wait = tonumber(soonest[2]) - now end
    if wait < 0 then wait = 0 end
    return {0, in_flight, wait}
end

redis.call('ZADD', KEYS[1], now + ttl, ARGV[4])
-- Keep the set itself alive past its longest-lived member so an idle clinic's
-- key does not linger in Redis for ever.
redis.call('PEXPIRE', KEYS[1], ttl * 2)
return {1, in_flight + 1, 0}
"""

# Re-label a held slot without disturbing its expiry.
#   KEYS[1] the clinic's lease set  ARGV[1] old token  ARGV[2] new token
#
# A slot is taken before the call is placed, when the only name available is a
# locally generated one. Once the provider returns its call id the slot is
# re-labelled to it, so the outcome handler — which knows the call id and
# nothing else — can hand the slot back by name.
_REKEY_SLOT_LUA = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score then
    return 0
end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZADD', KEYS[1], score, ARGV[2])
return 1
"""

# Fixed-window send rate.
#   KEYS[1] the window counter
#   ARGV[1] limit  ARGV[2] window_ms
#
# Increments first and compares after, so two workers racing on the same window
# cannot both read "under the limit" and both send.
#
# Returns {allowed, wait_ms}.
_SEND_RATE_LUA = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('PEXPIRE', KEYS[1], window)
end
if count > limit then
    local ttl = redis.call('PTTL', KEYS[1])
    if ttl < 0 then ttl = window end
    return {0, ttl}
end
return {1, 0}
"""


class OutboundLimits:
    """Cluster-wide call-concurrency and provider send-rate limits.

    Construction is cheap and does no I/O. Pass a client to share a connection
    or to substitute a fake in tests; otherwise one is built lazily.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        key_prefix: str = "ob",
        call_concurrency: int = _DEFAULT_CALL_CONCURRENCY,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        provider_per_minute: dict[SendProvider, int] | None = None,
        clock: Any = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._key_prefix = key_prefix
        self._call_concurrency = max(1, call_concurrency)
        self._lease_ms = max(1, lease_seconds) * 1000
        self._provider_per_minute = provider_per_minute or {
            SendProvider.TWILIO: _DEFAULT_TWILIO_PER_MINUTE,
            SendProvider.EMAIL: _DEFAULT_EMAIL_PER_MINUTE,
        }
        self._clock = clock or _wall_clock_ms

    async def _redis(self) -> Any:
        if self._client is None:
            url = settings.effective_redis_url or "redis://localhost:6379/0"
            self._client = from_url(url, encoding="utf-8", decode_responses=True)
        return self._client

    def _slots_key(self, institution_id: str) -> str:
        return f"{self._key_prefix}:calls:{institution_id}"

    def _rate_key(self, provider: str, scope_id: str, window: int) -> str:
        return f"{self._key_prefix}:rate:{provider}:{scope_id}:{window}"

    # ── concurrency ─────────────────────────────────────────────────────

    async def acquire_call_slot(
        self, institution_id: str, *, ceiling: int | None = None
    ) -> tuple[LimitDecision, ConcurrencySlot | None]:
        """Take a call slot for this clinic, or report how long to hold for.

        The returned slot must be handed to ``release_call_slot`` when the call
        finishes. Failing to is not a correctness problem — the lease expires —
        but it holds a slot out of circulation until it does.
        """
        limit = max(1, ceiling or self._call_concurrency)
        token = uuid.uuid4().hex
        key = self._slots_key(institution_id)
        try:
            client = await self._redis()
            result = await client.eval(
                _ACQUIRE_SLOT_LUA,
                1,
                key,
                str(self._clock()),
                str(limit),
                str(self._lease_ms),
                token,
            )
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.warning(
                "outbound call limiter unavailable, allowing call: "
                "institution=%s err=%s",
                institution_id, type(exc).__name__,
            )
            return LimitDecision(True), None

        granted = bool(int(result[0]))
        in_flight = int(result[1])
        if not granted:
            return (
                LimitDecision(
                    False,
                    retry_after_seconds=_ms_to_seconds(int(result[2])),
                    reason=f"call_concurrency_limit_{limit}",
                    in_flight=in_flight,
                ),
                None,
            )
        return (
            LimitDecision(True, in_flight=in_flight),
            ConcurrencySlot(institution_id=institution_id, token=token),
        )

    async def release_call_slot(self, slot: ConcurrencySlot) -> None:
        await self.release_call_slot_by_token(slot.institution_id, slot.token)

    async def release_call_slot_by_token(
        self, institution_id: str, token: str
    ) -> None:
        """Give a slot back. Safe to call twice, and safe to never call."""
        if not token:
            return
        try:
            client = await self._redis()
            await client.zrem(self._slots_key(institution_id), token)
        except Exception as exc:  # noqa: BLE001 — the lease expires regardless
            logger.warning(
                "outbound call limiter unavailable, slot left to expire: "
                "institution=%s err=%s",
                institution_id, type(exc).__name__,
            )

    async def rekey_call_slot(
        self, institution_id: str, old_token: str, new_token: str
    ) -> bool:
        """Rename a held slot, keeping its expiry. False if it already lapsed."""
        if not old_token or not new_token or old_token == new_token:
            return False
        try:
            client = await self._redis()
            result = await client.eval(
                _REKEY_SLOT_LUA,
                1,
                self._slots_key(institution_id),
                old_token,
                new_token,
            )
            return bool(int(result))
        except Exception as exc:  # noqa: BLE001 — the lease expires regardless
            logger.warning(
                "outbound call limiter unavailable, slot left under its old "
                "name: institution=%s err=%s",
                institution_id, type(exc).__name__,
            )
            return False

    async def in_flight_calls(self, institution_id: str) -> int:
        """Live slot count, expired leases excluded. For diagnostics."""
        try:
            client = await self._redis()
            await client.zremrangebyscore(
                self._slots_key(institution_id), "-inf", self._clock()
            )
            return int(await client.zcard(self._slots_key(institution_id)))
        except Exception:  # noqa: BLE001
            return 0

    # ── send rate ───────────────────────────────────────────────────────

    async def check_send_rate(
        self, provider: SendProvider | str, scope_id: str
    ) -> LimitDecision:
        """Reserve one send against the provider's per-minute ceiling."""
        provider_value = (
            provider.value if isinstance(provider, SendProvider) else str(provider)
        )
        try:
            limit = self._provider_per_minute[SendProvider(provider_value)]
        except (KeyError, ValueError):
            return LimitDecision(True)
        if limit <= 0:  # 0 disables the limit rather than blocking everything
            return LimitDecision(True)

        now_ms = self._clock()
        key = self._rate_key(provider_value, scope_id, now_ms // 60_000)
        try:
            client = await self._redis()
            result = await client.eval(_SEND_RATE_LUA, 1, key, str(limit), "60000")
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.warning(
                "outbound send-rate limiter unavailable, allowing send: "
                "provider=%s scope=%s err=%s",
                provider_value, scope_id, type(exc).__name__,
            )
            return LimitDecision(True)

        if int(result[0]):
            return LimitDecision(True)
        return LimitDecision(
            False,
            retry_after_seconds=_ms_to_seconds(int(result[1])),
            reason=f"{provider_value}_send_rate_{limit}_per_minute",
        )

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


def _ms_to_seconds(milliseconds: int) -> int:
    """Round up, so a caller never retries fractionally early."""
    if milliseconds <= 0:
        return 0
    return int(math.ceil(milliseconds / 1000))
