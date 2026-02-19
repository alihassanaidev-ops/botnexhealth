"""Tenant-scoped in-memory event broker for call-data freshness signals."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class TenantCallEventBroker:
    """Publishes and subscribes to lightweight tenant call-data events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, tenant_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber queue for a tenant."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers[tenant_id].add(queue)
        return queue

    async def unsubscribe(self, tenant_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue for a tenant."""
        async with self._lock:
            subscribers = self._subscribers.get(tenant_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(tenant_id, None)

    async def publish(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Publish an event to all tenant subscribers.

        Returns:
            Number of active subscribers that accepted the event.
        """
        event = {
            "type": event_type,
            "tenant_id": tenant_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": payload or {},
        }
        async with self._lock:
            queues = list(self._subscribers.get(tenant_id, set()))

        sent = 0
        for queue in queues:
            try:
                queue.put_nowait(event)
                sent += 1
            except asyncio.QueueFull:
                # Drop stale events for slow clients; next event will re-sync.
                continue
        return sent


call_event_broker = TenantCallEventBroker()

