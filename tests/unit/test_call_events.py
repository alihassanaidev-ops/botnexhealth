"""Tests for tenant-scoped call event broker."""

from __future__ import annotations

import asyncio

import pytest

from src.app.services.call_events import TenantCallEventBroker


@pytest.mark.asyncio
async def test_publish_delivers_to_tenant_subscriber():
    broker = TenantCallEventBroker()
    queue = await broker.subscribe("tenant-1")

    sent = await broker.publish("tenant-1", "data_changed", {"call_id": "c1"})
    assert sent == 1

    event = await asyncio.wait_for(queue.get(), timeout=0.2)
    assert event["type"] == "data_changed"
    assert event["tenant_id"] == "tenant-1"
    assert event["payload"]["call_id"] == "c1"

    await broker.unsubscribe("tenant-1", queue)


@pytest.mark.asyncio
async def test_publish_is_tenant_scoped():
    broker = TenantCallEventBroker()
    queue_a = await broker.subscribe("tenant-a")
    queue_b = await broker.subscribe("tenant-b")

    sent = await broker.publish("tenant-a", "data_changed", {"call_id": "ca"})
    assert sent == 1

    event_a = await asyncio.wait_for(queue_a.get(), timeout=0.2)
    assert event_a["tenant_id"] == "tenant-a"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue_b.get(), timeout=0.1)

    await broker.unsubscribe("tenant-a", queue_a)
    await broker.unsubscribe("tenant-b", queue_b)

