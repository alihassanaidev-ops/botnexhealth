from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.app.services.automation.retell_chat_client import (
    RetellChatAmbiguousError,
    RetellChatClient,
    RetellChatPermanentError,
)
from src.app.services.automation.retell_sms_conversation_service import (
    agent_response_text,
)


class _FakeAsyncClient:
    def __init__(self, *, response=None, exc=None):
        self.response = response
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, *args, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


def _response(status: int, body: dict | None = None):
    response = MagicMock()
    response.status_code = status
    response.text = "provider error"
    response.json.return_value = body or {}
    return response


def _with_client(coro, *, response=None, exc=None):
    fake = _FakeAsyncClient(response=response, exc=exc)
    with patch(
        "src.app.services.automation.retell_chat_client.httpx.AsyncClient",
        MagicMock(return_value=fake),
    ):
        return asyncio.run(coro)


def test_create_chat_maps_chat_id() -> None:
    client = RetellChatClient("key")
    result = _with_client(
        client.create_chat(
            agent_id="agent-1",
            agent_version=2,
            dynamic_variables={"clinic_name": "Example"},
            metadata={"workflow_run_id": "run-1"},
        ),
        response=_response(201, {"chat_id": "chat-1", "chat_status": "ongoing"}),
    )
    assert result.chat_id == "chat-1"
    assert result.status == "ongoing"


def test_completion_returns_only_structured_messages() -> None:
    client = RetellChatClient("key")
    result = _with_client(
        client.create_completion(chat_id="chat-1", content="Hello"),
        response=_response(
            200,
            {"messages": [{"role": "agent", "content": "Hi", "message_id": "m1"}]},
        ),
    )
    assert result[0].content == "Hi"
    assert result[0].message_id == "m1"


def test_mutating_timeout_is_ambiguous_and_not_retryable() -> None:
    client = RetellChatClient("key")
    with pytest.raises(RetellChatAmbiguousError):
        _with_client(
            client.create_completion(chat_id="chat-1", content="Hello"),
            exc=httpx.TimeoutException("timeout"),
        )


def test_missing_chat_id_is_permanent() -> None:
    client = RetellChatClient("key")
    with pytest.raises(RetellChatPermanentError):
        _with_client(
            client.create_chat(
                agent_id="agent-1",
                agent_version=None,
                dynamic_variables={},
                metadata={},
            ),
            response=_response(200, {}),
        )


def test_agent_response_ignores_non_agent_messages_and_bounds_length() -> None:
    from src.app.services.automation.retell_chat_client import RetellChatMessage

    text, ids = agent_response_text(
        (
            RetellChatMessage(role="user", content="private inbound", message_id="u1"),
            RetellChatMessage(role="agent", content="x" * 500, message_id="a1"),
        ),
        max_segments=1,
    )

    assert "private inbound" not in text
    assert len(text) == 160
    assert ids == ["a1"]
