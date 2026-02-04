"""Main Sikka API client (facade pattern)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.app.nexhealth.token_manager import InMemoryTokenCache, TokenManager
from src.app.sikka.auth import AuthConfig, AuthService
from src.app.sikka.exceptions import SikkaError
from src.app.sikka.http_client import SikkaHTTPClient

logger = logging.getLogger(__name__)


class SikkaClient:
    """
    Main client for Sikka API.

    Follows SOLID principles:
    - Single Responsibility: Orchestrates auth, token management, and HTTP requests
    - Dependency Inversion: Depends on abstractions (AuthConfig, TokenManager)

    Authentication Flow:
    1. Use App-Id and App-Key to get authorized practices
    2. For a specific practice, use office_id and secret_key to generate request_key
    3. Use request_key for all API calls (expires in ~24 hours)
    """

    def __init__(
        self,
        config: AuthConfig,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        """
        Initialize Sikka client.

        Args:
            config: Authentication configuration with app_id, app_secret, base_url
            office_id: Practice office ID (required for practice-specific API calls)
            secret_key: Practice secret key (required for generating request_key)
        """
        self._config = config
        self._office_id = office_id
        self._secret_key = secret_key
        # Per-practice token managers (keyed by office_id)
        self._token_managers: dict[str, TokenManager] = {}
        self._http_client: SikkaHTTPClient | None = None
        self._auth_service: AuthService | None = None
        self._auth_http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SikkaClient":
        # Initialize HTTP client for API requests
        self._http_client = SikkaHTTPClient(
            base_url=self._config.base_url,
            api_version=self._config.api_version,
        )
        await self._http_client.__aenter__()

        # Initialize auth service with separate httpx client
        self._auth_http_client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=30.0,
        )
        self._auth_service = AuthService(self._config, self._auth_http_client)

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http_client:
            await self._http_client.__aexit__(exc_type, exc, tb)
        if self._auth_http_client:
            await self._auth_http_client.aclose()

    def _get_token_manager(self, office_id: str) -> TokenManager:
        """Get or create token manager for a specific practice."""
        if office_id not in self._token_managers:
            self._token_managers[office_id] = TokenManager(InMemoryTokenCache())
        return self._token_managers[office_id]

    async def get_authorized_practices(self) -> list[dict]:
        """
        Get list of practices that have authorized this application.

        This uses App-Id and App-Key (no request_key needed).

        Returns:
            List of practice objects with office_id and secret_key

        Raises:
            SikkaError: If not initialized or authentication fails
        """
        if not self._auth_service:
            raise SikkaError("Client not initialized. Use as context manager.")

        return await self._auth_service.get_authorized_practices()

    async def _get_request_key(self, office_id: str, secret_key: str) -> str:
        """
        Get valid request key for a practice, fetching if necessary.

        Args:
            office_id: Practice office ID
            secret_key: Practice secret key

        Returns:
            Valid request_key

        Raises:
            SikkaError: If authentication fails
        """
        if not self._auth_service:
            raise SikkaError("Client not initialized. Use as context manager.")

        token_manager = self._get_token_manager(office_id)

        async def fetch_token():
            return await self._auth_service.generate_request_key(office_id, secret_key)

        return await token_manager.get_valid_token(fetch_token)

    def set_practice(self, office_id: str, secret_key: str) -> None:
        """
        Set the default practice for API calls.

        Args:
            office_id: Practice office ID
            secret_key: Practice secret key
        """
        self._office_id = office_id
        self._secret_key = secret_key

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """GET request."""
        return await self.request("GET", path, params=params, office_id=office_id, secret_key=secret_key)

    async def post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """POST request."""
        return await self.request("POST", path, params=params, json=json, office_id=office_id, secret_key=secret_key)

    async def patch(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """PATCH request."""
        return await self.request("PATCH", path, params=params, json=json, office_id=office_id, secret_key=secret_key)

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """DELETE request."""
        return await self.request("DELETE", path, params=params, office_id=office_id, secret_key=secret_key)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        office_id: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Make API request to Sikka.

        Args:
            method: HTTP method
            path: API path (with or without /v4 prefix)
            params: Query parameters
            json: JSON body
            office_id: Practice office ID (uses default if not provided)
            secret_key: Practice secret key (uses default if not provided)

        Returns:
            Response payload

        Raises:
            SikkaError: If client not initialized or request fails
        """
        if not self._http_client:
            raise SikkaError("Client not initialized. Use as context manager.")

        # Use provided or default practice credentials
        oid = office_id or self._office_id
        skey = secret_key or self._secret_key

        if not oid or not skey:
            raise SikkaError(
                "office_id and secret_key required for API calls. "
                "Call set_practice() or pass them to the request."
            )

        request_key = await self._get_request_key(oid, skey)

        # Add API version prefix if not present
        if not path.startswith(f"/{self._config.api_version}"):
            path = f"/{self._config.api_version}{path}"

        return await self._http_client.request(
            method,
            path,
            request_key=request_key,
            params=params,
            json=json,
        )
