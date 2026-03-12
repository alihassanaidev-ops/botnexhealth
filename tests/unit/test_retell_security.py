"""Unit tests for Retell signature verification."""

import pytest
from fastapi import HTTPException, Request
from retell.lib.webhook_auth import symmetric
from src.app.retell.security import RetellSignatureVerifier, get_signature_dependency

class TestRetellSecurity:
    
    def test_verify_signature_valid(self, retell_verifier):
        """Test valid signature verification."""
        payload = '{"event": "call_started"}'
        secret = "test-secret"
        
        # Calculate valid signature using Retell SDK helper
        signature = symmetric["sign"](payload, secret)
        
        assert retell_verifier.verify_signature(payload, signature) is True

    def test_verify_signature_invalid(self, retell_verifier):
        """Test invalid signature rejection."""
        payload = '{"event": "call_started"}'
        signature = "invalid-signature"
        
        assert retell_verifier.verify_signature(payload, signature) is False
        
    def test_verify_signature_no_secret(self):
        """Test verification rejects when no secret configured."""
        verifier = RetellSignatureVerifier(None)
        assert verifier.verify_signature("data", "sig") is False
        
    def test_verify_signature_missing_header(self, retell_verifier):
        """Test missing signature header."""
        assert retell_verifier.verify_signature(b"data", None) is False

@pytest.mark.asyncio
async def test_dependency_valid_signature():
    """Test FastAPI dependency with valid signature."""
    payload = b'test-body'
    secret = "test-secret"
    signature = symmetric["sign"](payload.decode("utf-8"), secret)
    
    # Mock Request
    scope = {
        "type": "http",
        "headers": [
            (b"x-retell-signature", signature.encode()),
        ],
    }
    
    async def receive():
        return {"type": "http.request", "body": payload}
        
    request = Request(scope, receive)
    
    # Get dependency
    dependency = get_signature_dependency(lambda: secret)
    
    # Should return body bytes
    result = await dependency(request)
    assert result == payload

@pytest.mark.asyncio
async def test_dependency_invalid_signature():
    """Test FastAPI dependency raises 401 on invalid signature."""
    payload = b'test-body'
    secret = "test-secret"
    signature = "invalid"
    
    scope = {
        "type": "http",
        "headers": [
            (b"x-retell-signature", signature.encode()),
        ],
    }
    
    async def receive():
        return {"type": "http.request", "body": payload}
        
    request = Request(scope, receive)
    
    dependency = get_signature_dependency(lambda: secret)
    
    with pytest.raises(HTTPException) as exc:
        await dependency(request)
    
    assert exc.value.status_code == 401
