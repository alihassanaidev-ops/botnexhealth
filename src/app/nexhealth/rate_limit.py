"""Distributed rate limiter for NexHealth API calls.

NexHealth limits each API key to:
  - 100 req/s globally (10 req/s for ``GET /appointments`` and
    ``GET /appointment_slots``)
  - 1000 req/min for patients + appointments endpoints
  - 2000 req/min for other endpoints

We run multiple Fargate tasks × multiple gunicorn workers — each one
would maintain its own in-process counter, and a fleet of N workers
multiplies the published rate by N. A single misbehaving tenant could
also exhaust their own budget for everyone else (their key is shared
across our cluster).

This module is the cluster-wide coordinator: a Redis-backed token
counter, atomically checked + incremented via a Lua script so multiple
workers can race without double-counting.

Design choices:

  - **Three buckets per request**: a global per-second cap (100), a
    class per-second cap (only relevant for the 10/s slot/appointment
    GETs), and a class per-minute cap (1000 or 2000 depending on the
    endpoint family). All three must accept before we let the request
    through.

  - **Fixed-window counters keyed by floor(now)**: simpler than sliding
    window, fast to evaluate in Lua, and the boundary-burst issue is
    bounded — at worst we send 2x the limit across a window boundary,
    and NexHealth's own 429 + ``Retry-After`` is the safety net.

  - **Fail-open on Redis errors**: if Redis is unreachable the limiter
    logs a warning and lets the request through. We refuse to make a
    Redis outage cascade into "no NexHealth traffic at all"; the
    existing reactive 429 handler in ``http_client.py`` still applies.

  - **API key never lands in Redis keys**: we hash the key (SHA-256,
    first 16 hex chars) so the namespace is tenant-scoped without
    leaking credential material into Redis instrumentation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Endpoint classification ──────────────────────────────────────────────


@dataclass(frozen=True)
class EndpointPolicy:
    """Rate-limit budgets for a request, per NexHealth's documented limits.

    A request is admitted only when ALL counters allow it. ``global_per_s``
    is shared with every other request from the same API key; the class
    counters are scoped to the endpoint family.
    """

    class_name: str
    global_per_s: int
    class_per_s: int
    class_per_min: int


# Documented NexHealth limits:
#   - 100 req/s globally; 10 req/s for slot/appointment GETs.
#   - 1000 req/min for patients + appointments endpoints.
#   - 2000 req/min for everything else.
_GLOBAL_PER_S = 100
_SLOW_READ_PER_S = 10
_PATIENTS_APPTS_PER_M = 1000
_OTHER_PER_M = 2000


def classify_endpoint(method: str, path: str) -> EndpointPolicy:
    """Map a NexHealth request to its rate-limit policy.

    The path is matched against the documented endpoint families; the
    method matters only for the slot/appointment GET sub-cap.
    """
    method_upper = (method or "GET").upper()
    is_appts_or_slots = path.startswith("/appointments") or path.startswith(
        "/appointment_slots"
    )
    is_patients = path.startswith("/patients")

    if method_upper == "GET" and is_appts_or_slots:
        # Documented 10 req/s sub-cap (appointment + slot reads).
        return EndpointPolicy(
            class_name="appts_read",
            global_per_s=_GLOBAL_PER_S,
            class_per_s=_SLOW_READ_PER_S,
            class_per_min=_PATIENTS_APPTS_PER_M,
        )
    if is_patients or is_appts_or_slots:
        # Patients + non-GET appointments share the 1000/min budget.
        return EndpointPolicy(
            class_name="patients_appts",
            global_per_s=_GLOBAL_PER_S,
            class_per_s=_GLOBAL_PER_S,
            class_per_min=_PATIENTS_APPTS_PER_M,
        )
    return EndpointPolicy(
        class_name="other",
        global_per_s=_GLOBAL_PER_S,
        class_per_s=_GLOBAL_PER_S,
        class_per_min=_OTHER_PER_M,
    )


# ── Errors ──────────────────────────────────────────────────────────────


class NexHealthLocalRateLimitExceeded(Exception):
    """Raised when we couldn't acquire a slot within the configured deadline.

    This is local pre-flight — the request never went out. Callers should
    treat it as a 503-equivalent (back off and try later).
    """

    def __init__(self, *, api_key_id: str, class_name: str, waited_ms: int) -> None:
        super().__init__(
            f"NexHealth local rate limit exceeded for tenant {api_key_id} "
            f"class={class_name} after {waited_ms}ms"
        )
        self.api_key_id = api_key_id
        self.class_name = class_name
        self.waited_ms = waited_ms


# ── Lua script ──────────────────────────────────────────────────────────


# Atomically check + increment N counters in lock-step.
#
# KEYS[1..N]: counter keys (one per bucket)
# ARGV layout: pairs of (limit, ttl_ms) for each KEY, in the same order.
#   ARGV[2i-1] = limit for KEYS[i]
#   ARGV[2i]   = TTL milliseconds to set on first INCR for KEYS[i]
#
# Returns:
#   0          → all buckets accepted; counters incremented.
#   ms_to_wait → at least one bucket is full; the value is the PTTL of the
#                first full bucket (i.e., milliseconds until its window
#                resets). When PTTL returns -1/-2 (no TTL or missing key,
#                shouldn't happen for an over-limit counter, but just in
#                case) we fall back to the bucket's nominal TTL.
_RATE_LIMIT_LUA = """
local n = #KEYS
for i = 1, n do
    local count = tonumber(redis.call('GET', KEYS[i]) or '0')
    local limit = tonumber(ARGV[2*i - 1])
    if count >= limit then
        local ttl = redis.call('PTTL', KEYS[i])
        if ttl < 0 then ttl = tonumber(ARGV[2*i]) end
        return ttl
    end
end

for i = 1, n do
    local ttl_ms = tonumber(ARGV[2*i])
    if redis.call('INCR', KEYS[i]) == 1 then
        redis.call('PEXPIRE', KEYS[i], ttl_ms)
    end
end

return 0
"""


# ── Limiter ─────────────────────────────────────────────────────────────


class NexHealthRateLimiter:
    """Cluster-wide pre-flight rate limiter for NexHealth requests.

    Construction is cheap; no network I/O. ``acquire()`` does the work.
    Pass an ``async_redis`` (e.g., ``redis.asyncio.from_url(...)``) — the
    limiter doesn't manage the client lifecycle and assumes the caller
    handles connection setup/teardown.
    """

    _SCRIPT_SHA: str | None = None  # set lazily on first call per process

    def __init__(
        self,
        async_redis: Any,
        *,
        key_prefix: str = "nh:rl",
        max_wait_ms: int = 5000,
        clock: Any = None,
    ) -> None:
        self._redis = async_redis
        self._key_prefix = key_prefix
        self._max_wait_ms = max_wait_ms
        self._clock = clock or _wall_clock_ms

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Stable, non-reversible identifier for use as a Redis key prefix."""
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]

    async def acquire(
        self,
        api_key_id: str,
        method: str,
        path: str,
        *,
        max_wait_ms: int | None = None,
    ) -> None:
        """Block until the request is allowed, or raise after the deadline.

        On Redis errors the call is fail-open: a warning is logged and the
        request is allowed through. The reactive 429 handler in
        ``NexHealthHTTPClient.request`` is the safety net.
        """
        policy = classify_endpoint(method, path)
        deadline_ms = self._clock() + (max_wait_ms or self._max_wait_ms)
        total_waited_ms = 0

        while True:
            try:
                wait_ms = await self._try_acquire(api_key_id, policy)
            except Exception as exc:  # noqa: BLE001 — fail-open is intentional
                logger.warning(
                    "NexHealth rate limiter unreachable (failing open): "
                    "tenant=%s class=%s err=%s",
                    api_key_id,
                    policy.class_name,
                    type(exc).__name__,
                )
                return

            if wait_ms == 0:
                return

            now_ms = self._clock()
            if now_ms + wait_ms > deadline_ms:
                raise NexHealthLocalRateLimitExceeded(
                    api_key_id=api_key_id,
                    class_name=policy.class_name,
                    waited_ms=total_waited_ms,
                )

            # Add a small jitter (10–80ms) so a thundering herd of waiters
            # spreads across the next window edge instead of all firing at
            # exactly the same instant.
            jitter_ms = random.randint(10, 80)
            sleep_ms = wait_ms + jitter_ms
            await asyncio.sleep(sleep_ms / 1000)
            total_waited_ms += sleep_ms

    async def _try_acquire(
        self, api_key_id: str, policy: EndpointPolicy
    ) -> int:
        now_ms = self._clock()
        sec_window = now_ms // 1000
        min_window = now_ms // 60_000

        # Bucket layout — order MUST match the ARGV pairs below.
        global_s_key = f"{self._key_prefix}:{api_key_id}:global:s:{sec_window}"
        class_s_key = (
            f"{self._key_prefix}:{api_key_id}:{policy.class_name}:s:{sec_window}"
        )
        class_m_key = (
            f"{self._key_prefix}:{api_key_id}:{policy.class_name}:m:{min_window}"
        )

        keys = [global_s_key, class_s_key, class_m_key]
        # ARGV layout: (limit, ttl_ms) per key.
        argv = [
            str(policy.global_per_s), "1000",
            str(policy.class_per_s), "1000",
            str(policy.class_per_min), "60000",
        ]
        result = await self._redis.eval(_RATE_LIMIT_LUA, len(keys), *keys, *argv)
        return int(result)


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)
