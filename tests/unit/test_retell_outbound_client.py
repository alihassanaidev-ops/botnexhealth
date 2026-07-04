"""Unit tests for RetellOutboundClient error classification (Plan 03 / XC-1b).

Verifies the create-phone-call error mapping that drives retry vs at-most-once:
  4xx → permanent · 5xx → transient (retry) · timeout/network → ambiguous (no retry).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.app.services.automation.retell_outbound_client import (
    RetellAmbiguousError,
    RetellOutboundClient,
    RetellPermanentError,
    RetellTransientError,
)


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient used as an async context manager."""

    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._result


def _run(*, result=None, exc=None):
    fake = _FakeAsyncClient(result=result, exc=exc)
    with patch(
        "src.app.services.automation.retell_outbound_client.httpx.AsyncClient",
        MagicMock(return_value=fake),
    ):
        return asyncio.run(
            RetellOutboundClient("re_key").create_phone_call(
                from_number="+15005550000",
                to_number="+14165551234",
                override_agent_id="agent_x",
                dynamic_variables={},
                metadata={},
            )
        )


def _resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body or {})
    return r


def test_timeout_raises_ambiguous():
    with pytest.raises(RetellAmbiguousError):
        _run(exc=httpx.TimeoutException("timed out"))


def test_transport_error_raises_ambiguous():
    with pytest.raises(RetellAmbiguousError):
        _run(exc=httpx.ConnectError("conn refused"))


def test_5xx_raises_transient():
    with pytest.raises(RetellTransientError):
        _run(result=_resp(503))


def test_4xx_raises_permanent():
    with pytest.raises(RetellPermanentError):
        _run(result=_resp(422))


def test_200_returns_call_id():
    result = _run(result=_resp(200, {"call_id": "call_abc", "call_status": "registered"}))
    assert result.call_id == "call_abc"
    assert result.call_status == "registered"
