"""Redis-backed event bus for institution-scoped SSE notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from redis import ConnectionPool, Redis
from redis.asyncio import from_url as async_from_url

from src.app.config import settings

logger = logging.getLogger(__name__)

EVENT_CHANNEL_PREFIX = "sse:institution"
SSE_TICKET_PREFIX = "sse:ticket"
SSE_TICKET_TTL_SECONDS = 30

SUPPORTED_EVENT_TYPES = frozenset(
    {
        "calls_updated",
        "callbacks_updated",
        "dashboard_updated",
        "notification",
    }
)

# ── Lazy sync Redis pool (shared across Celery tasks and API process) ────────

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _redis_url() -> str:
    redis_url = settings.effective_redis_url
    if not redis_url:
        raise RuntimeError("REDIS_URL or CELERY_BROKER_URL must be configured")
    return redis_url


def _get_sync_client() -> Redis:
    """Return a Redis client backed by a shared connection pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool.from_url(
                    _redis_url(),
                    max_connections=10,
                    decode_responses=True,
                )
    return Redis(connection_pool=_pool)


def _channel_name(institution_id: str) -> str:
    return f"{EVENT_CHANNEL_PREFIX}:{institution_id}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Publish ──────────────────────────────────────────────────────────────────

def publish_event(institution_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
    """Publish a lightweight institution-scoped event to Redis."""
    if not institution_id:
        raise ValueError("institution_id is required")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"Unsupported SSE event type: {event_type}")

    payload = {
        "type": event_type,
        "timestamp": _utc_now(),
        "data": data or {},
    }

    _get_sync_client().publish(
        _channel_name(institution_id),
        json.dumps(payload),
    )


# ── Subscribe (async, one connection per SSE client) ─────────────────────────

async def subscribe_events(institution_id: str) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed institution-scoped events from Redis pub/sub."""
    if not institution_id:
        raise ValueError("institution_id is required")

    client = async_from_url(
        _redis_url(),
        encoding="utf-8",
        decode_responses=True,
    )
    pubsub = client.pubsub()
    channel = _channel_name(institution_id)

    try:
        await pubsub.subscribe(channel)

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message:
                await asyncio.sleep(0.1)
                continue

            if message.get("type") != "message":
                continue

            raw_payload = message.get("data")
            if not isinstance(raw_payload, str):
                logger.warning("Ignoring non-text SSE payload on channel %s", channel)
                continue

            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed SSE payload on channel %s", channel)
                continue

            if not isinstance(payload, dict):
                logger.warning("Ignoring non-object SSE payload on channel %s", channel)
                continue

            event_type = payload.get("type")
            if event_type not in SUPPORTED_EVENT_TYPES:
                logger.warning("Ignoring unsupported SSE event type on channel %s: %s", channel, event_type)
                continue

            yield payload
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(channel)
        with suppress(Exception):
            await pubsub.aclose()
        with suppress(Exception):
            await client.aclose()


# ── SSE ticket helpers (avoids JWT in query strings) ─────────────────────────

def create_sse_ticket(user_id: str, institution_id: str) -> str:
    """Create a short-lived single-use ticket for SSE authentication."""
    ticket = secrets.token_urlsafe(32)
    key = f"{SSE_TICKET_PREFIX}:{ticket}"
    value = json.dumps({"user_id": user_id, "institution_id": institution_id})
    _get_sync_client().setex(key, SSE_TICKET_TTL_SECONDS, value)
    return ticket


async def redeem_sse_ticket(ticket: str) -> dict[str, str] | None:
    """Redeem a ticket, returning the payload and deleting it (single-use)."""
    key = f"{SSE_TICKET_PREFIX}:{ticket}"
    client = async_from_url(
        _redis_url(),
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        pipe = client.pipeline()
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
        raw = results[0]
        if not raw:
            return None
        return json.loads(raw)
    finally:
        await client.aclose()
