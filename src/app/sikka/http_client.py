"""HTTP client for Sikka API with retry and error handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.app.sikka.exceptions import (
    SikkaAPIError,
    SikkaAuthenticationError,
    SikkaConflictError,
    SikkaRateLimitError,
    SikkaResourceNotFoundError,
)

logger = logging.getLogger(__name__)


class SikkaHTTPClient:
    """HTTP client with retry logic and error handling for Sikka API."""

    def __init__(
        self,
        base_url: str,
        api_version: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._base_url = base_url
        self._api_version = api_version
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client: httpx.AsyncClient | None = None

    def _build_headers(self, request_key: str) -> dict[str, str]:
        """
        Build standard headers for Sikka API requests.

        Uses request_key as the authentication token.
        """
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "request-key": request_key,
        }

    def _handle_error_response(self, payload: dict[str, Any]) -> None:
        """
        Handle Sikka API error responses based on error codes.

        Sikka error response structure:
        {
            "http_code": 401,
            "http_code_desc": "Unauthorized",
            "error_code": "API2003",
            "short_message": "Authentication/Authorization Failed",
            "long_message": "ID or key is incorrect.",
            "more_information": "..."
        }
        """
        error_code = payload.get("error_code", "UNKNOWN")
        http_code = payload.get("http_code", 500)
        short_msg = payload.get("short_message", "API Error")
        long_msg = payload.get("long_message", "")
        more_info = payload.get("more_information", "")

        error_message = f"{short_msg}: {long_msg}" if long_msg else short_msg
        if more_info:
            error_message += f" ({more_info})"

        # Authentication errors (API2003, API2004, API2007)
        if error_code in ("API2003", "API2004", "API2007"):
            raise SikkaAuthenticationError(
                error_message,
                error_code=error_code,
                http_code=http_code,
                short_message=short_msg,
                long_message=long_msg,
            )

        # Rate limit errors (API2011, API2012)
        if error_code in ("API2011", "API2012"):
            raise SikkaRateLimitError(
                error_message,
                error_code=error_code,
                retry_after=60,  # Default to 60 seconds
            )

        # Resource not found (API2009)
        if error_code == "API2009":
            raise SikkaResourceNotFoundError(
                error_message,
                error_code=error_code,
                http_code=http_code,
            )

        # Conflict errors (API2013, API2015)
        if error_code in ("API2013", "API2015"):
            raise SikkaConflictError(
                error_message,
                error_code=error_code,
                http_code=http_code,
            )

        # Generic API error
        raise SikkaAPIError(
            error_message,
            error_code=error_code,
            http_code=http_code,
            short_message=short_msg,
            long_message=long_msg,
        )

    async def __aenter__(self) -> "SikkaHTTPClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        request_key: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make HTTP request with automatic retry for rate limits.

        Args:
            method: HTTP method
            path: API path
            request_key: Sikka request key (session token)
            params: Query parameters
            json: JSON body

        Returns:
            Response payload

        Raises:
            SikkaRateLimitError: If rate limit exceeded after retries
            SikkaAPIError: If API returns error response
        """
        if not self._client:
            raise RuntimeError("HTTP client not initialized. Use as context manager.")

        headers = self._build_headers(request_key)
        # Sikka uses request-key in headers for auth

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json=json,
                )

                # Handle rate limiting (429)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self._retry_delay))
                    if attempt < self._max_retries:
                        logger.warning(
                            f"Rate limit hit (attempt {attempt + 1}/{self._max_retries + 1}), "
                            f"retrying after {retry_after}s"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    # Parse error response if available
                    try:
                        error_payload = response.json()
                        self._handle_error_response(error_payload)
                    except Exception:
                        raise SikkaRateLimitError(
                            f"Rate limit exceeded after {self._max_retries + 1} attempts",
                            retry_after=retry_after,
                        )

                response.raise_for_status()
                payload = response.json()

                # Check for Sikka error in response
                if "error_code" in payload:
                    self._handle_error_response(payload)

                return payload

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    continue  # Will be handled above

                # Try to parse error response
                try:
                    error_payload = e.response.json()
                    self._handle_error_response(error_payload)
                except Exception:
                    logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
                    raise

            except httpx.RequestError as e:
                logger.error(f"Request error: {e}")
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                    continue
                raise

        raise RuntimeError("Unexpected retry loop exit")
