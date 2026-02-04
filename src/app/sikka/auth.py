"""Authentication service for Sikka API."""

from __future__ import annotations

from typing import Protocol

import httpx

from src.app.sikka.exceptions import SikkaAuthenticationError


class AuthConfig(Protocol):
    """Protocol for Sikka authentication configuration."""

    @property
    def app_id(self) -> str:
        """Sikka Application ID."""
        ...

    @property
    def app_secret(self) -> str:
        """Sikka Application Secret Key."""
        ...

    @property
    def base_url(self) -> str:
        """Sikka API base URL."""
        ...

    @property
    def api_version(self) -> str:
        """API version."""
        ...


class AuthService:
    """Handles Sikka API authentication."""

    def __init__(self, config: AuthConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = http_client

    def _build_auth_headers(self) -> dict[str, str]:
        """
        Build headers for Sikka API authentication requests.

        Sikka uses App-Id and App-Key headers for authentication.
        """
        return {
            "Content-Type": "application/json",
            "App-Id": self._config.app_id,
            "App-Key": self._config.app_secret,
        }

    async def get_authorized_practices(self) -> list[dict]:
        """
        Get list of practices that have authorized this application.

        Returns:
            List of practice objects containing office_id and secret_key

        Raises:
            SikkaAuthenticationError: If authentication fails
        """
        if not self._config.app_id or not self._config.app_secret:
            raise SikkaAuthenticationError("Missing Sikka App ID or App Secret")

        headers = self._build_auth_headers()
        response = await self._client.get(
            f"/{self._config.api_version}/authorized_practices",
            headers=headers,
        )
        response.raise_for_status()

        payload = response.json()

        # Sikka returns paginated response with 'items' array
        if isinstance(payload, list):
            return payload
        elif isinstance(payload, dict) and "items" in payload:
            return payload["items"]
        elif isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        else:
            raise SikkaAuthenticationError(f"Unexpected response format: {payload}")

    async def generate_request_key(
        self,
        office_id: str,
        secret_key: str,
    ) -> tuple[str, int]:
        """
        Generate a request key (session token) for a specific practice.

        Args:
            office_id: Practice office ID from authorized_practices
            secret_key: Practice secret key from authorized_practices

        Returns:
            Tuple of (request_key, expires_in_seconds)

        Raises:
            SikkaAuthenticationError: If request key generation fails
        """
        if not self._config.app_id or not self._config.app_secret:
            raise SikkaAuthenticationError("Missing Sikka App ID or App Secret")

        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "request_key",
            "office_id": office_id,
            "secret_key": secret_key,
            "app_id": self._config.app_id,
            "app_key": self._config.app_secret,
        }

        response = await self._client.post(
            f"/{self._config.api_version}/request_key",
            headers=headers,
            json=body,
        )
        response.raise_for_status()

        payload = response.json()

        if "request_key" not in payload:
            raise SikkaAuthenticationError(f"No request_key in response: {payload}")

        request_key = payload["request_key"]

        # Parse expires_in (format: "85603 second(s)")
        expires_str = payload.get("expires_in", "3600 second(s)")
        expires_in = int(expires_str.split()[0])

        return request_key, expires_in
