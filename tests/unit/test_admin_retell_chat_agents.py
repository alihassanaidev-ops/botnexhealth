from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import HTTPException

from src.app.api.routes import admin_institutions


@pytest.mark.asyncio
@respx.mock
async def test_list_retell_chat_agents_uses_chat_api(monkeypatch) -> None:
    monkeypatch.setattr(admin_institutions.settings, "retell_api_secret", "test-key")
    route = respx.get(
        "https://api.retellai.com/list-chat-agents",
        params={"is_latest": "true", "limit": "1000"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "agent_id": "agent_chat",
                    "agent_name": "SMS assistant",
                    "version": 7,
                    "is_published": True,
                }
            ],
        )
    )

    agents = await admin_institutions.list_retell_chat_agents(SimpleNamespace())

    assert route.called
    assert agents[0].agent_id == "agent_chat"
    assert agents[0].channel == "chat"
    assert agents[0].version == 7
    assert agents[0].is_published is True


@pytest.mark.asyncio
@respx.mock
async def test_verify_retell_chat_agent_uses_chat_api(monkeypatch) -> None:
    monkeypatch.setattr(admin_institutions.settings, "retell_api_secret", "test-key")
    route = respx.get(
        "https://api.retellai.com/get-chat-agent/agent_chat"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"agent_id": "agent_chat", "agent_name": "SMS assistant"},
        )
    )

    result = await admin_institutions.verify_retell_chat_agent(
        "agent_chat", SimpleNamespace()
    )

    assert route.called
    assert result["agent_id"] == "agent_chat"


@pytest.mark.asyncio
@respx.mock
async def test_verify_retell_chat_agent_maps_not_found(monkeypatch) -> None:
    monkeypatch.setattr(admin_institutions.settings, "retell_api_secret", "test-key")
    respx.get("https://api.retellai.com/get-chat-agent/missing").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_institutions.verify_retell_chat_agent(
            "missing", SimpleNamespace()
        )

    assert exc_info.value.status_code == 404
