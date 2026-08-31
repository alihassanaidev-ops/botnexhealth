"""Cluster-wide circuit breaker for outbound service calls (Item 17).

When an outside service — the voice provider, the messaging provider, the email
provider, the practice-software API — starts failing, every queued send still
calls out, waits for its timeout, fails, and retries. A campaign with hundreds of
patients queued produces hundreds of doomed requests: workers tied up, retry
budget burned, and *other* clinics' work stuck in the queue behind them.

Several single-request protections already exist and are good — the NexHealth
fleet-wide rate limiter, the three-way voice error classification, the
practice-data read health check. None of them remembers anything. This module is
the layer that remembers a service has been failing and stops trying for a while.

Design choices, and why:

  - **Per service per clinic.** One clinic's bad Twilio subaccount must not stop
    every other clinic from texting. ``scope_id`` is caller-supplied: pass the
    narrowest identity that shares a credential — a location for Twilio/Retell,
    an institution for NexHealth.

  - **State in Redis, not in the process.** The app runs as multiple Fargate
    tasks x multiple workers. If each copy tracked failures independently they
    would disagree about whether a service is down and the protection would be
    unreliable — a breaker that is open on one worker and closed on the next
    protects nothing.

  - **Every transition is decided inside a Lua script.** Workers race on the same
    breaker constantly. Read-then-write from Python would let two workers both
    see "one failure below threshold" and both increment, or let a dozen probes
    through at once when the cooldown expires. The scripts here are the only
    place breaker state changes.

  - **One probe at a time in half-open.** When the cooldown expires the breaker
    does not simply close — it admits a single request, held by a ``SET NX``
    token with its own TTL, and waits for that outcome. Success closes the
    breaker; failure re-opens it for a fresh cooldown. The token's TTL is the
    backstop for a probe whose caller dies before reporting either way.

  - **Fail open when Redis is unreachable**, following the convention already set
    by ``nexhealth/rate_limit.py``. A warning is logged and the call proceeds. An
    outage in the safety mechanism must not become a total outage — the whole
    point of this module is to stop one dead dependency taking everything with
    it, and it would be a poor joke for the breaker itself to do exactly that.

  - **Only the caller knows what counts as a failure.** This module never
    inspects an exception. A 4xx is a bad request, not a sick service, and
    counting it would trip the breaker on a malformed payload that will fail
    just as fast next time. Callers record a failure for the transient/server
    class only — the same distinction ``RetellTransientError`` and the SMS
    ``_classify`` helper already draw.

Work refused by an open breaker is **held and retried**, never dropped: the
dispatcher defers the run to ``retry_after_seconds`` on a timer, exactly as it
defers a send that arrives during quiet hours. No campaign run fails because a
supplier had an outage.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from redis.asyncio import from_url

from src.app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "BreakerService",
    "BreakerState",
    "BreakerDecision",
    "ServiceBreaker",
    "NoOpCircuitBreaker",
    "CircuitBreaker",
    "breaker_service_for_node",
]


class BreakerService(str, Enum):
    """The outside services a breaker can be opened against.

    Values appear in Redis keys and in the alert logs Item 35 alarms on, so they
    are part of the operational contract — renaming one silently resets every
    breaker for that service and orphans its alarm.
    """

    RETELL = "retell"
    TWILIO = "twilio"
    EMAIL = "email"
    NEXHEALTH = "nexhealth"


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    #: Redis itself was unreachable and the call was allowed through. Distinct
    #: from CLOSED so callers and dashboards can tell "healthy" from "unknown".
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BreakerDecision:
    """Whether a call may proceed, and how long to hold it if not."""

    allowed: bool
    state: BreakerState
    #: How long to defer the work for. Zero when allowed.
    retry_after_seconds: int = 0


#: Which service a send node's channel actually talks to. Kept here rather than
#: in the dispatcher so a new channel registers its breaker alongside its
#: executor instead of the mapping drifting out of sight.
_NODE_TYPE_SERVICES: dict[str, BreakerService] = {
    "send_sms": BreakerService.TWILIO,
    "send_email": BreakerService.EMAIL,
    "send_voice": BreakerService.RETELL,
}


def breaker_service_for_node(node_type: str) -> BreakerService | None:
    """The service a send node calls, or None for node types that call nobody."""
    return _NODE_TYPE_SERVICES.get(node_type)


@runtime_checkable
class ServiceBreaker(Protocol):
    """The contract the dispatcher and send executors depend on.

    Mirrors ``ComplianceGate``: a protocol plus a no-op stub, so the engine runs
    without a shared store in unit tests while production wires the real one.
    """

    async def allow(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerDecision: ...

    async def record_failure(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState: ...

    async def record_success(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState: ...


class NoOpCircuitBreaker:
    """Default stub — always allows and remembers nothing.

    Deliberately not a thin wrapper over the real breaker with Redis missing:
    that would log a fail-open warning on every send in every unit test and
    train everyone to ignore the one warning that matters.
    """

    async def allow(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerDecision:
        return BreakerDecision(True, BreakerState.CLOSED)

    async def record_failure(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState:
        return BreakerState.CLOSED

    async def record_success(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState:
        return BreakerState.CLOSED


# Defaults. Deliberately conservative: a breaker that trips on a single blip
# causes more harm than the blip. Five failures inside a minute is a pattern,
# not a coincidence.
_DEFAULT_FAILURE_THRESHOLD = 5
_DEFAULT_FAILURE_WINDOW_S = 60
_DEFAULT_COOLDOWN_S = 60
# How long after the cooldown the breaker stays willing to probe. Without this
# an idle breaker would linger half-open forever and the first call after a
# quiet hour would be treated as a probe.
_DEFAULT_HALF_OPEN_S = 300
# A probe holder that never reports back must not wedge the breaker shut.
# Comfortably longer than the 15s vendor timeouts, short enough that a dead
# worker costs one cooldown rather than an outage.
_DEFAULT_PROBE_TIMEOUT_S = 60


# ── Lua ─────────────────────────────────────────────────────────────────
#
# Shared key layout for every script below:
#   KEYS[1] open      present  → breaker is open, TTL is the remaining cooldown
#   KEYS[2] halfopen  present without KEYS[1] → breaker is half-open
#   KEYS[3] probe     the single in-flight probe token while half-open
#   KEYS[4] fails     rolling failure counter while closed

# ARGV[1] probe_ttl_ms
_ALLOW_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    local ttl = redis.call('PTTL', KEYS[1])
    if ttl < 0 then ttl = 0 end
    return {0, 'open', ttl}
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    if redis.call('SET', KEYS[3], '1', 'NX', 'PX', tonumber(ARGV[1])) then
        return {1, 'half_open', 0}
    end
    local ttl = redis.call('PTTL', KEYS[3])
    if ttl < 0 then ttl = tonumber(ARGV[1]) end
    return {0, 'half_open', ttl}
end
return {1, 'closed', 0}
"""

# ARGV[1] threshold, ARGV[2] window_ms, ARGV[3] cooldown_ms, ARGV[4] half_open_ms
#
# Returns {state, transitioned} — ``transitioned`` is 1 only on the call that
# actually opened the breaker, so the caller raises one alert per outage rather
# than one per rejected request.
_FAILURE_LUA = """
local cooldown = tonumber(ARGV[3])
local half_open = tonumber(ARGV[4])

local function trip()
    redis.call('SET', KEYS[1], '1', 'PX', cooldown)
    redis.call('SET', KEYS[2], '1', 'PX', cooldown + half_open)
    redis.call('DEL', KEYS[3])
    redis.call('DEL', KEYS[4])
end

if redis.call('EXISTS', KEYS[1]) == 1 then
    -- Already open. A straggler from before the trip; nothing to decide.
    return {'open', 0}
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    -- Half-open and the probe failed: straight back to open, fresh cooldown.
    trip()
    return {'open', 1}
end

local n = redis.call('INCR', KEYS[4])
if n == 1 then
    redis.call('PEXPIRE', KEYS[4], tonumber(ARGV[2]))
end
if n >= tonumber(ARGV[1]) then
    trip()
    return {'open', 1}
end
return {'closed', 0}
"""

# Returns {state, recovered} — ``recovered`` is 1 when this success closed a
# breaker that was open or half-open, which is the recovery alert.
_SUCCESS_LUA = """
local recovered = 0
if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('EXISTS', KEYS[2]) == 1 then
    recovered = 1
end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3], KEYS[4])
return {'closed', recovered}
"""


def _text(value: Any) -> str:
    """Lua string replies arrive as bytes unless the client decodes responses."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class CircuitBreaker:
    """Shared failure tracking per service per clinic.

    Construction is cheap and does no I/O. Pass a client to share one connection
    (and to substitute a fake in tests); leave it out and one is built lazily
    from settings, as the campaign send limiter does.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        key_prefix: str = "cb",
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        failure_window_seconds: int = _DEFAULT_FAILURE_WINDOW_S,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_S,
        half_open_seconds: int = _DEFAULT_HALF_OPEN_S,
        probe_timeout_seconds: int = _DEFAULT_PROBE_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._key_prefix = key_prefix
        self._threshold = max(1, failure_threshold)
        self._window_ms = max(1, failure_window_seconds) * 1000
        self._cooldown_ms = max(1, cooldown_seconds) * 1000
        self._half_open_ms = max(1, half_open_seconds) * 1000
        self._probe_ttl_ms = max(1, probe_timeout_seconds) * 1000

    # ── plumbing ────────────────────────────────────────────────────────

    async def _redis(self) -> Any:
        if self._client is None:
            url = settings.effective_redis_url or "redis://localhost:6379/0"
            self._client = from_url(url, encoding="utf-8", decode_responses=True)
        return self._client

    def _keys(self, service: BreakerService | str, scope_id: str) -> list[str]:
        service_value = service.value if isinstance(service, BreakerService) else service
        base = f"{self._key_prefix}:{service_value}:{scope_id}"
        return [f"{base}:open", f"{base}:halfopen", f"{base}:probe", f"{base}:fails"]

    # ── the three calls ─────────────────────────────────────────────────

    async def allow(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerDecision:
        """Ask whether a call to *service* for *scope_id* may go out.

        A half-open breaker admits exactly one caller, which then owes the
        breaker a ``record_success`` or ``record_failure``.
        """
        keys = self._keys(service, scope_id)
        try:
            client = await self._redis()
            result = await client.eval(
                _ALLOW_LUA, len(keys), *keys, str(self._probe_ttl_ms)
            )
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            logger.warning(
                "circuit breaker unavailable, allowing call: service=%s scope=%s err=%s",
                service, scope_id, type(exc).__name__,
            )
            return BreakerDecision(True, BreakerState.UNAVAILABLE)

        allowed = bool(int(result[0]))
        state = BreakerState(_text(result[1]))
        retry_after = _ms_to_seconds(int(result[2]))
        if not allowed:
            logger.info(
                "circuit breaker holding call: service=%s scope=%s state=%s retry_after=%ss",
                service, scope_id, state.value, retry_after,
            )
        return BreakerDecision(allowed, state, retry_after)

    async def record_failure(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState:
        """Count a failure that indicates the *service* is unwell.

        Only the transient/server class belongs here. A 4xx is a bad request,
        not a sick service; counting it trips the breaker on a payload bug.
        """
        keys = self._keys(service, scope_id)
        try:
            client = await self._redis()
            result = await client.eval(
                _FAILURE_LUA,
                len(keys),
                *keys,
                str(self._threshold),
                str(self._window_ms),
                str(self._cooldown_ms),
                str(self._half_open_ms),
            )
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.warning(
                "circuit breaker unavailable, failure not recorded: "
                "service=%s scope=%s err=%s",
                service, scope_id, type(exc).__name__,
            )
            return BreakerState.UNAVAILABLE

        state = BreakerState(_text(result[0]))
        if int(result[1]):
            # One line per outage, not per rejected call — this is the alert.
            logger.error(
                "circuit breaker OPENED: service=%s scope=%s cooldown=%ss "
                "threshold=%s/%ss",
                service, scope_id, self._cooldown_ms // 1000,
                self._threshold, self._window_ms // 1000,
            )
        return state

    async def record_success(
        self, service: BreakerService | str, scope_id: str
    ) -> BreakerState:
        """Report a call that worked. Closes the breaker and clears the count."""
        keys = self._keys(service, scope_id)
        try:
            client = await self._redis()
            result = await client.eval(_SUCCESS_LUA, len(keys), *keys)
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.warning(
                "circuit breaker unavailable, success not recorded: "
                "service=%s scope=%s err=%s",
                service, scope_id, type(exc).__name__,
            )
            return BreakerState.UNAVAILABLE

        if int(result[1]):
            logger.error(
                "circuit breaker CLOSED (service recovered): service=%s scope=%s",
                service, scope_id,
            )
        return BreakerState(_text(result[0]))

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _ms_to_seconds(milliseconds: int) -> int:
    """Round *up*, so a caller never re-probes fractionally early."""
    if milliseconds <= 0:
        return 0
    return int(math.ceil(milliseconds / 1000))
