"""Unit tests for Plan 03 — Outbound Voice (VoiceNodeExecutor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.services.automation.definition_schema import SendVoiceNode


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_run(contact_id="c-1", location_id="l-1", institution_id="inst-1"):
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = institution_id
    run.contact_id = contact_id
    run.location_id = location_id
    return run


def _make_node(agent_id="agent_abc", next_id="node-2"):
    return SendVoiceNode(
        id="node-1",
        retell_agent_id=agent_id,
        next_node_id=next_id,
    )


def _make_contact(phone="+14165551234", first="Jane"):
    c = MagicMock()
    c.phone = phone
    c.first_name = first
    c.last_name = "Doe"
    return c


def _make_location(retell_from_number="+15005550000"):
    loc = MagicMock()
    loc.retell_from_number = retell_from_number
    return loc


def _make_executor(contact=None, location=None):
    from src.app.services.automation.voice_node_executor import VoiceNodeExecutor

    session = AsyncMock()
    runtime = AsyncMock()

    async def _get(model, pk):
        from src.app.models.contact import Contact
        from src.app.models.institution_location import InstitutionLocation
        if model is Contact:
            return contact
        if model is InstitutionLocation:
            return location
        return None

    session.get = AsyncMock(side_effect=_get)
    runtime.begin_step = AsyncMock(return_value=MagicMock())
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()

    return VoiceNodeExecutor(session, runtime), runtime


def _fail_reason(runtime) -> str:
    return runtime.fail_run.call_args.kwargs.get("reason", "")


def _mock_retell_client(post_side_effect):
    """Return a patched httpx.AsyncClient whose .post uses post_side_effect."""
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=post_side_effect)
    return mock_http


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_executor_fails_when_no_contact_id():
    executor, runtime = _make_executor()
    run = _make_run(contact_id=None)
    asyncio.run(executor.execute(run, _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no contact_id" in _fail_reason(runtime)


def test_executor_fails_when_contact_not_found():
    executor, runtime = _make_executor(contact=None)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "not found" in _fail_reason(runtime)


def test_executor_fails_when_no_phone():
    contact = _make_contact(phone=None)
    executor, runtime = _make_executor(contact=contact)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no phone" in _fail_reason(runtime)


def test_executor_fails_when_no_retell_from_number():
    contact = _make_contact()
    location = _make_location(retell_from_number=None)
    executor, runtime = _make_executor(contact=contact, location=location)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "retell_from_number" in _fail_reason(runtime)


def test_executor_fails_when_retell_not_configured():
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)

    with patch("src.app.services.automation.voice_node_executor.settings") as mock_settings:
        mock_settings.retell_api_secret = None
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    runtime.fail_run.assert_called_once()
    assert "Retell not configured" in _fail_reason(runtime)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_executor_places_call_success():
    contact = _make_contact(phone="+14165551234", first="Jane")
    location = _make_location(retell_from_number="+15005550000")
    executor, runtime = _make_executor(contact=contact, location=location)

    captured = {}

    async def _fake_post(url, headers, json):
        captured["url"] = url
        captured["payload"] = json
        resp = MagicMock()
        resp.status_code = 201
        return resp

    with (
        patch("src.app.services.automation.voice_node_executor.settings") as mock_settings,
        patch("src.app.services.automation.voice_node_executor.httpx.AsyncClient") as MockClient,
    ):
        mock_settings.retell_api_secret = "re_secret"
        MockClient.return_value = _mock_retell_client(_fake_post)
        result = asyncio.run(executor.execute(_make_run(), _make_node(agent_id="agent_xyz"), {}))

    assert result == "node-2"
    payload = captured["payload"]
    assert payload["from_number"] == "+15005550000"
    assert payload["to_number"] == "+14165551234"
    assert payload["override_agent_id"] == "agent_xyz"
    assert payload["retell_llm_dynamic_variables"]["first_name"] == "Jane"
    assert payload["retell_llm_dynamic_variables"]["user_number"] == "+14165551234"
    assert payload["metadata"]["workflow_run_id"] == "run-1"
    assert payload["metadata"]["source"] == "outbound_campaign"
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "call_placed"
    runtime.fail_run.assert_not_called()


def test_executor_fails_on_retell_http_error():
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)

    async def _fake_post(url, headers, json):
        resp = MagicMock()
        resp.status_code = 422
        resp.text = "Unprocessable"
        return resp

    with (
        patch("src.app.services.automation.voice_node_executor.settings") as mock_settings,
        patch("src.app.services.automation.voice_node_executor.httpx.AsyncClient") as MockClient,
    ):
        mock_settings.retell_api_secret = "re_secret"
        MockClient.return_value = _mock_retell_client(_fake_post)
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_voice error" in _fail_reason(runtime)
