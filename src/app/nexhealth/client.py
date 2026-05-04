"""Main NexHealth API client (facade pattern)."""

from __future__ import annotations

from typing import Any

from src.app.nexhealth.auth import AuthConfig, AuthService
from src.app.nexhealth.exceptions import NexHealthError
from src.app.nexhealth.http_client import NexHealthHTTPClient
from src.app.nexhealth.rate_limit import NexHealthRateLimiter
from src.app.nexhealth.token_manager import TokenManager


class NexHealthClient:
    """
    Main client for NexHealth API.

    Follows SOLID principles:
    - Single Responsibility: Orchestrates auth, token management, and HTTP requests
    - Dependency Inversion: Depends on abstractions (AuthConfig, TokenManager)
    - Open/Closed: Extensible via TokenCache implementations
    """

    def __init__(
        self,
        config: AuthConfig,
        token_manager: TokenManager | None = None,
        http_client: NexHealthHTTPClient | None = None,
        rate_limiter: NexHealthRateLimiter | None = None,
    ) -> None:
        self._config = config
        self._token_manager = token_manager or TokenManager()
        self._http_client = http_client
        self._auth_service: AuthService | None = None
        self._rate_limiter = rate_limiter

    async def __aenter__(self) -> "NexHealthClient":
        # Initialize HTTP client if not provided (for dependency injection in tests)
        if not self._http_client:
            api_key_id = (
                NexHealthRateLimiter.hash_api_key(self._config.api_key)
                if self._config.api_key
                else None
            )
            self._http_client = NexHealthHTTPClient(
                base_url=self._config.base_url,
                accept_header=self._config.accept_header,
                api_version=self._config.api_version,
                max_keepalive_connections=getattr(
                    self._config, "nexhealth_max_keepalive_connections", 10
                ),
                max_connections=getattr(self._config, "nexhealth_max_connections", 20),
                rate_limiter=self._rate_limiter,
                api_key_id=api_key_id,
            )
            await self._http_client.__aenter__()

        # Initialize auth service with a separate httpx client for auth requests
        # (auth doesn't use bearer token, uses API key directly)
        import httpx
        auth_http_client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=30.0,
        )
        self._auth_service = AuthService(self._config, auth_http_client)
        self._auth_http_client = auth_http_client

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http_client:
            await self._http_client.__aexit__(exc_type, exc, tb)
        if hasattr(self, "_auth_http_client") and self._auth_http_client:
            await self._auth_http_client.aclose()

    async def _get_token(self) -> str:
        """Get valid token, fetching if necessary."""
        if not self._auth_service:
            raise NexHealthError("Client not initialized. Use as context manager.")

        async def fetch_token():
            return await self._auth_service.authenticate()

        return await self._token_manager.get_valid_token(fetch_token)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET request."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST request."""
        return await self.request("POST", path, params=params, json=json)

    async def patch(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH request."""
        return await self.request("PATCH", path, params=params, json=json)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """DELETE request."""
        return await self.request("DELETE", path, params=params)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make API request.

        Args:
            method: HTTP method
            path: API path
            params: Query parameters
            json: JSON body

        Returns:
            Response payload
        """
        if not self._http_client:
            raise NexHealthError("Client not initialized. Use as context manager.")

        token = await self._get_token()
        return await self._http_client.request(method, path, token=token, params=params, json=json)
