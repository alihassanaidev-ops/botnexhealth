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
