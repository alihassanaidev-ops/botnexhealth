"""Authentication service for NexHealth API."""

from __future__ import annotations

from typing import Protocol

import httpx

from src.app.nexhealth.exceptions import NexHealthAuthenticationError


class AuthConfig(Protocol):
    """Protocol for authentication configuration."""

    @property
    def api_key(self) -> str:
        """NexHealth API key."""
        ...

    @property
    def base_url(self) -> str:
        """NexHealth API base URL."""
        ...

    @property
    def accept_header(self) -> str:
        """Accept header value."""
        ...

    @property
    def api_version(self) -> str:
        """API version header value."""
        ...


class AuthService:
    """Handles NexHealth API authentication."""

    def __init__(self, config: AuthConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = http_client

    def _build_auth_headers(self) -> dict[str, str]:
        """
        Build headers for authentication request (DRY).
        
        Note: For /authenticates endpoint, pass API key directly in Authorization header
        (NOT with "Bearer" prefix). The response will contain a bearer token to use
        for all subsequent API requests.
        """
        return {
            "Accept": self._config.accept_header,
            "Authorization": self._config.api_key,  # API key directly, no "Bearer" prefix
            "Nex-Api-Version": self._config.api_version,
        }

    async def authenticate(self) -> tuple[str, int]:
        """
        Authenticate with NexHealth API.
        
        Makes a POST request to /authenticates with the API key in the Authorization header.
        The response contains a bearer token valid for 1 hour that must be used for
        all subsequent API requests (with "Bearer " prefix).

        Returns:
            Tuple of (bearer_token, expires_in_seconds)
            
        Raises:
            NexHealthAuthenticationError: If authentication fails
        """
        if not self._config.api_key:
            raise NexHealthAuthenticationError("Missing NexHealth API key")

        headers = self._build_auth_headers()
        response = await self._client.post("/authenticates", headers=headers)
        response.raise_for_status()

        payload = response.json()
        if not payload.get("code") or "data" not in payload or "token" not in payload["data"]:
            raise NexHealthAuthenticationError(f"Authentication failed: {payload}")

        token = payload["data"]["token"]
        # Tokens are valid for 1 hour (3600 seconds)
        return token, 3600
