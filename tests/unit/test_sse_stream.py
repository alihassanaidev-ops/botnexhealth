"""Unit tests for the SSE endpoint's stream lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from src.app.api.routes import sse as sse_module


def _make_request(disconnected_sequence: list[bool]) -> Any:
    """Build a minimal Request stand-in whose is_disconnected() follows the given sequence."""

    iterator = iter(disconnected_sequence)

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            try:
                return next(iterator)
            except StopIteration:
                return True

    return _FakeRequest()


async def _consume_stream(stream: AsyncIterator[str], max_items: int = 20) -> list[str]:
    collected: list[str] = []
    async for chunk in stream:
        collected.append(chunk)
        if len(collected) >= max_items:
            break
    return collected


async def _run_stream(
    *,
    subscribe_side_effect: Any,
    disconnected_sequence: list[bool],
    heartbeat_interval: float = 0.05,
) -> list[str]:
    request = _make_request(disconnected_sequence)

    with patch.object(sse_module, "subscribe_events", side_effect=subscribe_side_effect), \
        patch.object(sse_module, "redeem_sse_ticket", new=AsyncMock(
            return_value={"user_id": "u", "institution_id": "inst-1"}
        )), \
        patch.object(sse_module, "HEARTBEAT_INTERVAL_SECONDS", heartbeat_interval):
        response = await sse_module.stream_institution_events(
            request=request,  # type: ignore[arg-type]
            ticket="ticket-abc",
        )
        return await _consume_stream(response.body_iterator)


@pytest.mark.asyncio
async def test_stream_closes_when_subscribe_raises() -> None:
    """If subscribe_events raises (Redis down), the stream must terminate so the
    client reconnects, rather than staying open emitting only heartbeats."""

    async def failing_subscribe(_institution_id: str) -> AsyncIterator[dict[str, Any]]:
        raise RuntimeError("redis unavailable")
        yield  # pragma: no cover — make this an async generator

    chunks = await _run_stream(
        subscribe_side_effect=failing_subscribe,
        disconnected_sequence=[False] * 50,
    )

    # With reader failing immediately, the main loop breaks before emitting anything.
    # Allow at most a single heartbeat if the reader's finally hasn't set the event yet.
    non_heartbeat = [c for c in chunks if not c.startswith(":")]
    assert non_heartbeat == [], f"expected no data chunks, got: {non_heartbeat!r}"
    assert len(chunks) <= 1


@pytest.mark.asyncio
async def test_stream_delivers_events_from_reader() -> None:
    keep_subscription_open = asyncio.Event()

    async def good_subscribe(_institution_id: str) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "calls_updated", "data": {"x": 1}, "timestamp": "2026-01-01T00:00:00Z"}
        await keep_subscription_open.wait()

    chunks = await _run_stream(
        subscribe_side_effect=good_subscribe,
        disconnected_sequence=[False, False, False, True],
        heartbeat_interval=0.5,
    )

    data_chunks = [c for c in chunks if c.startswith("event:")]
    assert len(data_chunks) == 1
    assert "event: calls_updated" in data_chunks[0]
    assert "\"x\":1" in data_chunks[0]
    assert "\"timestamp\":\"2026-01-01T00:00:00Z\"" in data_chunks[0]


@pytest.mark.asyncio
async def test_stream_emits_heartbeat_when_idle() -> None:
    keep_subscription_open = asyncio.Event()

    async def idle_subscribe(_institution_id: str) -> AsyncIterator[dict[str, Any]]:
        await keep_subscription_open.wait()
        if False:
            yield {}  # pragma: no cover — make this an async generator

    chunks = await _run_stream(
        subscribe_side_effect=idle_subscribe,
        disconnected_sequence=[False, False, True],
        heartbeat_interval=0.05,
    )

    assert any(c.startswith(":") for c in chunks), f"expected heartbeat, got {chunks!r}"


@pytest.mark.asyncio
async def test_ticket_redemption_failure_returns_401() -> None:
    from fastapi import HTTPException

    request = _make_request([False])

    with patch.object(sse_module, "redeem_sse_ticket", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as excinfo:
            await sse_module.stream_institution_events(
                request=request,  # type: ignore[arg-type]
                ticket="bad-ticket",
            )
    assert excinfo.value.status_code == 401
