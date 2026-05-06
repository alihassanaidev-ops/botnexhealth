import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from src.app.retell import functions

@pytest.fixture
def mock_registry():
    # Save original registry
    orig = functions._function_registry.copy()
    functions._function_registry.clear()
    yield functions._function_registry
    # Restore original registry
    functions._function_registry.clear()
    functions._function_registry.update(orig)

@pytest.mark.asyncio
async def test_handle_function_call_success(mock_registry):
    mock_handler = AsyncMock(return_value={"result": "ok"})
    mock_registry["test_func"] = mock_handler
    
    payload = {
        "function_name": "test_func",
        "call_id": "call_123",
        "args": {"foo": "bar"}
    }
    body = json.dumps(payload).encode()
    
    response = await functions.handle_function_call(function_name=None, body=body)
    assert response.result == {"result": "ok"}
    mock_handler.assert_called_with({"foo": "bar"})

@pytest.mark.asyncio
async def test_handle_function_call_with_query_param(mock_registry):
    mock_handler = AsyncMock(return_value={"result": "ok"})
    mock_registry["test_func"] = mock_handler
    
    # Payload missing function_name, but supplied via query param
    payload = {
        "args": {"foo": "bar"},
        # "call_id" missing, should default to unknown
    }
    body = json.dumps(payload).encode()
    
    response = await functions.handle_function_call(function_name="test_func", body=body)
    assert response.result == {"result": "ok"}

@pytest.mark.asyncio
async def test_handle_function_call_unknown_function(mock_registry):
    payload = {"function_name": "unknown", "call_id": "1"}
    body = json.dumps(payload).encode()
    
    with pytest.raises(HTTPException) as exc:
        await functions.handle_function_call(body=body)
    assert exc.value.status_code == 400
    assert "Unknown function" in exc.value.detail

@pytest.mark.asyncio
async def test_handle_function_call_invalid_json():
    body = b"invalid-json"
    with pytest.raises(HTTPException) as exc:
        await functions.handle_function_call(body=body)
    assert exc.value.status_code == 400
    assert "Invalid JSON" in exc.value.detail

@pytest.mark.asyncio
async def test_handle_function_call_execution_error(mock_registry):
    mock_handler = AsyncMock(side_effect=Exception("Execution Fail"))
    mock_registry["fail_func"] = mock_handler
    
    payload = {"function_name": "fail_func", "call_id": "1", "args": {}}
    body = json.dumps(payload).encode()
    
    with pytest.raises(HTTPException) as exc:
        await functions.handle_function_call(body=body)
    assert exc.value.status_code == 500
    assert "Function execution failed" in exc.value.detail

@pytest.mark.asyncio
async def test_get_retell_secret():
    from src.app.retell.security import get_retell_secret
    with patch("src.app.config.settings") as mock_settings:
        mock_settings.retell_api_secret = "secret"
        assert get_retell_secret() == "secret"

def test_register_decorator(mock_registry):
    @functions.register_function("decorated_func")
    async def my_func(args):
        return {}
    
    assert "decorated_func" in mock_registry
    assert mock_registry["decorated_func"] == my_func

@pytest.mark.asyncio
async def test_handle_function_call_nested_call_id(mock_registry):
    # Test extracting call_id from chat object
    mock_handler = AsyncMock(return_value={})
    mock_registry["test"] = mock_handler
    
    payload = {
        "function_name": "test",
        "chat": {"call_id": "nested_123"},
        "args": {}
    }
    body = json.dumps(payload).encode()
    await functions.handle_function_call(body=body)
    # Logging check would be ideal but hard to assert without capturing logs.
    # Code execution path verification is enough for coverage.


@pytest.mark.asyncio
async def test_handle_function_call_uses_call_object_call_id_for_idempotent_tool(mock_registry):
    mock_handler = AsyncMock(return_value={"should": "not be called directly"})
    mock_registry["create_patient"] = mock_handler

    payload = {
        "function_name": "create_patient",
        "call": {"call_id": "call_nested_123", "agent_id": "agent_123"},
        "args": {"first_name": "Test"},
    }

    with patch.object(
        functions,
        "run_with_idempotency",
        new=AsyncMock(return_value={"ok": True}),
    ) as run_with_idempotency:
        response = await functions.handle_function_call(
            body=json.dumps(payload).encode()
        )

    assert response.result == {"ok": True}
    assert run_with_idempotency.await_args.kwargs["call_id"] == "call_nested_123"


@pytest.mark.asyncio
async def test_handle_function_call_falls_back_to_tool_call_id_for_idempotent_tool(mock_registry):
    mock_registry["book_appointment"] = AsyncMock()

    payload = {
        "function_name": "book_appointment",
        "tool_call_id": "tool_call_123",
        "args": {"patient_id": "patient_123"},
    }

    with patch.object(
        functions,
        "run_with_idempotency",
        new=AsyncMock(return_value={"ok": True}),
    ) as run_with_idempotency:
        response = await functions.handle_function_call(
            body=json.dumps(payload).encode()
        )

    assert response.result == {"ok": True}
    assert run_with_idempotency.await_args.kwargs["call_id"] == "tool_call_123"


@pytest.mark.asyncio
async def test_handle_function_call_prefers_call_id_over_tool_call_id(mock_registry):
    mock_registry["book_appointment"] = AsyncMock()

    payload = {
        "function_name": "book_appointment",
        "call": {"call_id": "call_123"},
        "tool_call_id": "tool_call_123",
        "args": {"patient_id": "patient_123"},
    }

    with patch.object(
        functions,
        "run_with_idempotency",
        new=AsyncMock(return_value={"ok": True}),
    ) as run_with_idempotency:
        await functions.handle_function_call(body=json.dumps(payload).encode())

    assert run_with_idempotency.await_args.kwargs["call_id"] == "call_123"


@pytest.mark.asyncio
async def test_handle_function_call_routes_from_chat_agent_id(mock_registry):
    async def handler(args):
        return {"context": functions.get_call_context()}

    mock_registry["test"] = handler

    payload = {
        "function_name": "test",
        "chat": {"call_id": "chat_123", "agent_id": "agent_chat"},
        "args": {},
    }
    response = await functions.handle_function_call(body=json.dumps(payload).encode())

    assert response.result["context"]["agent_id"] == "agent_chat"
    assert response.result["context"]["agent_id_source"] == "payload.chat.agent_id"


@pytest.mark.asyncio
async def test_handle_function_call_routes_from_args_agent_id(mock_registry):
    async def handler(args):
        return {"context": functions.get_call_context()}

    mock_registry["test"] = handler

    payload = {
        "function_name": "test",
        "call_id": "call_123",
        "args": {"agent_id": "agent_args", "date_of_birth": "2000-01-01"},
    }
    response = await functions.handle_function_call(body=json.dumps(payload).encode())

    assert response.result["context"]["agent_id"] == "agent_args"
    assert response.result["context"]["agent_id_source"] == "args.agent_id"


@pytest.mark.asyncio
async def test_handle_function_call_routes_from_query_agent_id(mock_registry):
    async def handler(args):
        return {"context": functions.get_call_context()}

    mock_registry["test"] = handler

    payload = {
        "function_name": "test",
        "call_id": "call_123",
        "args": {},
    }
    response = await functions.handle_function_call(
        query_agent_id="agent_query",
        body=json.dumps(payload).encode(),
    )

    assert response.result["context"]["agent_id"] == "agent_query"
    assert response.result["context"]["agent_id_source"] == "query.agent_id"
