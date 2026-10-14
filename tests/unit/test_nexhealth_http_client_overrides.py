"""The per-request timeout/retry overrides must not change existing callers.

`NexHealthHTTPClient` is shared by every NexHealth path, including the
latency-sensitive voice-agent handlers. Adding the overrides meant replacing
`self._max_retries` with a local inside the retry loop, so these tests pin the
default behaviour: a caller that passes nothing must retry exactly as many
times as before and must not send a per-request timeout to httpx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.app.nexhealth.http_client import NexHealthHTTPClient


class _RecordingClient:
    """Stands in for httpx.AsyncClient, recording each call's kwargs."""

    def __init__(self, behaviour):
        self.calls: list[dict[str, Any]] = []
        self._behaviour = behaviour

    async def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return self._behaviour(len(self.calls))


# raise_for_status() needs a request attached, which hand-built responses lack.
_STUB_REQUEST = httpx.Request("GET", "https://example.test/stub")


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"code": True, "data": []}, request=_STUB_REQUEST)


def _make_client(behaviour, **kwargs) -> tuple[NexHealthHTTPClient, _RecordingClient]:
    client = NexHealthHTTPClient(
        base_url="https://example.test",
        accept_header="application/vnd.Nexhealth+json;version=2",
        api_version="v2",
        retry_delay=0,  # keep the test fast; backoff timing isn't under test
        **kwargs,
    )
    recorder = _RecordingClient(behaviour)
    client._client = recorder  # type: ignore[assignment]
    return client, recorder


@pytest.mark.asyncio
async def test_default_call_sends_no_per_request_timeout():
    """Unchanged callers must not start passing a timeout to httpx."""
    client, recorder = _make_client(lambda _n: _ok_response())

    await client.request("GET", "/patients", token="t")

    assert len(recorder.calls) == 1
    assert "timeout" not in recorder.calls[0]


@pytest.mark.asyncio
async def test_default_call_still_retries_the_configured_number_of_times():
    """A timeout is an httpx.RequestError, so the default path retries it."""

    def always_timeout(_n):
        raise httpx.ConnectTimeout("timed out")

    client, recorder = _make_client(always_timeout, max_retries=3)

    with pytest.raises(httpx.ConnectTimeout):
        await client.request("GET", "/providers", token="t")

    # 1 initial attempt + 3 retries, exactly as before the override existed.
    assert len(recorder.calls) == 4


@pytest.mark.asyncio
async def test_max_retries_zero_makes_a_single_attempt():
    """What the availability reads use: one slow try, no multiplication."""

    def always_timeout(_n):
        raise httpx.ConnectTimeout("timed out")

    client, recorder = _make_client(always_timeout, max_retries=3)

    with pytest.raises(httpx.ConnectTimeout):
        await client.request("GET", "/availabilities", token="t", max_retries=0)

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_timeout_override_reaches_httpx():
    client, recorder = _make_client(lambda _n: _ok_response())

    await client.request("GET", "/availabilities", token="t", timeout=60.0)

    assert recorder.calls[0]["timeout"] == 60.0


@pytest.mark.asyncio
async def test_override_does_not_leak_into_later_calls():
    """The override is per request, not sticky client state."""
    client, recorder = _make_client(lambda _n: _ok_response())

    await client.request("GET", "/availabilities", token="t", timeout=60.0, max_retries=0)
    await client.request("GET", "/patients", token="t")

    assert recorder.calls[0]["timeout"] == 60.0
    assert "timeout" not in recorder.calls[1]
    assert client._max_retries == 3


@pytest.mark.asyncio
async def test_rate_limit_retry_still_honours_the_default_budget():
    """The 429 branch also moved off self._max_retries — pin it too."""

    def rate_limited_then_ok(n):
        if n < 3:
            return httpx.Response(
                429, headers={"Retry-After": "0"}, json={}, request=_STUB_REQUEST
            )
        return _ok_response()

    client, recorder = _make_client(rate_limited_then_ok, max_retries=3)

    result = await client.request("GET", "/patients", token="t")

    assert result == {"code": True, "data": []}
    assert len(recorder.calls) == 3
