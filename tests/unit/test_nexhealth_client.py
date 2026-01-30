"""Unit tests for NexHealth client."""

import pytest
import respx
from httpx import Response
from src.app.nexhealth.client import NexHealthClient
from src.app.nexhealth.exceptions import NexHealthAuthenticationError, NexHealthRateLimitError

@pytest.fixture
def client(mock_settings):
    """NexHealthClient fixture."""
    return NexHealthClient(config=mock_settings)

@pytest.mark.asyncio
async def test_authentication_success(client, mock_settings):
    """Test successful authentication flow."""
    async with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        # Mock Auth endpoint
        respx_mock.post("/authenticates").mock(
            return_value=Response(
                201, 
                json={
                    "code": True, 
                    "data": {"token": "valid-token"}
                }
            )
        )
        
        # Test auth
        async with client:
             token = await client._get_token()
             
        assert token == "valid-token"

import httpx

@pytest.mark.asyncio
async def test_authentication_failure(client, mock_settings):
    """Test authentication failure raises exception."""
    async with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        # Mock Auth failure
        respx_mock.post("/authenticates").mock(
            return_value=Response(401, json={"code": False, "error": "Invalid key"})
        )
        
        async with client:
            with pytest.raises(httpx.HTTPStatusError):
                await client._get_token()

@pytest.mark.asyncio
async def test_api_request_success(client, mock_settings):
    """Test successful API request with token auto-injection."""
    async with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        # Mock Auth
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        
        # Mock API Endpoint
        route = respx_mock.get("/institutions").mock(
            return_value=Response(200, json={"code": True, "data": []})
        )
        
        async with client:
            result = await client.get("/institutions")
            
        assert result == {"code": True, "data": []}
        assert route.called
        # Verify Headers
        last_request = route.calls.last.request
        assert last_request.headers["Authorization"] == "Bearer token"

@pytest.mark.asyncio
async def test_rate_limit_retry(client, mock_settings):
    """Test rate limit handling with retries."""
    async with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        
        # First call returns 429, second returns 200
        route = respx_mock.get("/institutions").mock(
            side_effect=[
                Response(429, headers={"Retry-After": "0"}),
                Response(200, json={"data": "success"})
            ]
        )
        
        async with client:
            result = await client.get("/institutions")
            
        assert result["data"] == "success"
        assert route.call_count == 2
