"""Security utilities for Retell AI webhook/function verification."""

from __future__ import annotations

import hashlib
import logging
from typing import Callable

from fastapi import HTTPException, Request
from retell.lib.webhook_auth import verify as retell_verify

logger = logging.getLogger(__name__)


class RetellSignatureVerifier:
    """
    Verify Retell AI webhook/function call signatures.
    
    HIPAA Requirement: All incoming requests from Retell must be verified
    to ensure they haven't been tampered with.
    
    Uses retell-sdk for verification (validates timestamp and signature).
    """

    def __init__(self, api_key: str | None = None) -> None:
        """
        Initialize verifier with Retell API Key.
        
        Args:
            api_key: Retell API Key for signature verification
        """
        self._api_key = api_key

    def verify_signature(
        self,
        payload: str,
        signature: str | None,
    ) -> bool:
        """
        Verify the request signature using Retell SDK.
        
        Args:
            payload: Raw request body string
            signature: Signature from x-retell-signature header
            
        Returns:
            True if signature is valid, False otherwise
        """
        if not self._api_key:
            # No API key means we cannot verify — reject unconditionally.
            # Never allow unauthenticated callers to reach PHI-handling flows.
            # Set RETELL_API_SECRET in your environment to enable the webhook.
            logger.error(
                "Retell signature verification failed: RETELL_API_SECRET is not configured. "
                "Set this environment variable to enable webhook verification."
            )
            return False

        if not signature:
            logger.warning("Missing x-retell-signature header")
            return False

        try:
            masked_key = f"{self._api_key[:4]}...{self._api_key[-4:]}" if self._api_key else "None"
            logger.debug(f"Verifying Retell Signature. Key: {masked_key}")

            # Strategy 1: Verify Raw Payload
            if retell_verify(payload, self._api_key, signature):
                # Only log debug if it works, to reduce noise
                logger.debug("Signature matched with RAW payload")
                return True
                
            # Strategy 2: Verify Stripped Payload (remove trailing newlines common in some proxies)
            if retell_verify(payload.strip(), self._api_key, signature):
                logger.info("Signature matched with STRIPPED payload")
                return True
                
            # Strategy 3: Verify Canonical JSON (official docs recommendation)
            # This handles cases where wire format differs (spacing/formatting) from signed format
            try:
                import json
                if payload:
                    data = json.loads(payload)
                    canonical_payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
                    if retell_verify(canonical_payload, self._api_key, signature):
                        logger.info("Signature matched with CANONICAL JSON payload")
                        return True
            except Exception as e:
                # Log debug only to avoid log spam on failed attacks
                logger.debug(f"Failed to check canonical JSON signature: {e}")

            logger.warning("Retell signature verification failed (all strategies tried)")
            return False
        except Exception as e:
            logger.error(f"Error during Retell signature verification: {e}")
            return False


def get_retell_secret() -> str | None:
    """Get Retell secret from settings."""
    from src.app.config import settings
    return getattr(settings, "retell_api_secret", None)


def get_signature_dependency(api_key_getter: Callable[[], str | None]):
    """
    Create a FastAPI dependency for signature verification.
    
    Args:
        api_key_getter: Function that returns the Retell API Key
        
    Returns:
        FastAPI dependency function
    """
    async def verify_retell_signature(request: Request) -> bytes:
        """Verify Retell signature and return raw body."""
        body_bytes = await request.body()
        signature = request.headers.get("x-retell-signature")
        
        # Parse and re-dump body to match Retell's signing format
        # Documentation: json.dumps(post_data, separators=(",", ":"), ensure_ascii=False)
        try:
            if not body_bytes:
                # Handle empty body (GET/DELETE)
                body_str = ""
            else:
                # Use raw body string for verification to ensure we verify exactly what was signed
                # Re-serializing with json.dumps can alter usage of spaces/separators
                body_str = body_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to process request body for verification: {e}")
            raise HTTPException(status_code=400, detail="Invalid request body")

        api_key = api_key_getter()
        verifier = RetellSignatureVerifier(api_key)
        
        if not verifier.verify_signature(body_str, signature):
            logger.error("Invalid Retell signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        return body_bytes
    
    return verify_retell_signature


def hash_for_logging(value: str) -> str:
    """
    Hash a value for HIPAA-compliant logging.
    
    Use this for call_ids, patient_ids, etc in audit logs.
    Never log raw identifiers.
    
    Args:
        value: Value to hash
        
    Returns:
        SHA256 hash of the value (first 16 chars)
    """
    return hashlib.sha256(value.encode()).hexdigest()[:16]
