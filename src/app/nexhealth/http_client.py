"""HTTP client for NexHealth API with retry and rate limit handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.app.nexhealth.exceptions import NexHealthAPIError, NexHealthRateLimitError
from src.app.nexhealth.rate_limit import NexHealthRateLimiter

logger = logging.getLogger(__name__)


class NexHealthHTTPClient:
    """HTTP client with retry logic for NexHealth API."""

    def __init__(
        self,
        base_url: str,
        accept_header: str,
        api_version: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_keepalive_connections: int = 10,
        max_connections: int = 20,
        rate_limiter: NexHealthRateLimiter | None = None,
        api_key_id: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._accept_header = accept_header
        self._api_version = api_version
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._max_keepalive_connections = max_keepalive_connections
        self._max_connections = max_connections
        self._client: httpx.AsyncClient | None = None
        # Pre-flight rate limiter — coordinates concurrency across Fargate
        # tasks so we never burst NexHealth past its documented per-API-key
        # caps (100/s, 10/s for slot/appt GETs, 1000/min for patient/appt
        # endpoints, 2000/min otherwise). The reactive 429 retry below stays
        # as a safety net for the boundary-burst case + clock skew.
        self._rate_limiter = rate_limiter
        self._api_key_id = api_key_id

    def _build_headers(self, token: str) -> dict[str, str]:
        """
        Build standard headers for API requests (DRY).
        
        Note: For all API requests EXCEPT /authenticates, use the bearer token
        with "Bearer " prefix in the Authorization header.
        """
        return {
            "Accept": self._accept_header,
            "Authorization": f"Bearer {token}",  # Bearer token with "Bearer " prefix
            "Nex-Api-Version": self._api_version,
        }

    async def __aenter__(self) -> "NexHealthHTTPClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            limits=httpx.Limits(
                max_keepalive_connections=self._max_keepalive_connections,
                max_connections=self._max_connections,
            ),
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
        token: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make HTTP request with automatic retry for rate limits.

        Args:
            method: HTTP method
            path: API path
            token: Bearer token
            params: Query parameters
            json: JSON body

        Returns:
            Response payload

        Raises:
            NexHealthRateLimitError: If rate limit exceeded after retries
            NexHealthAPIError: If API returns error response
        """
        if not self._client:
            raise RuntimeError("HTTP client not initialized. Use as context manager.")

        headers = self._build_headers(token)

        for attempt in range(self._max_retries + 1):
            # Pre-flight: ask the cluster-wide limiter for a slot before we
            # spend a real network round-trip. The limiter fails open on
            # Redis errors, so this can't break us if the cache is down.
            if self._rate_limiter and self._api_key_id:
                await self._rate_limiter.acquire(self._api_key_id, method, path)

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
                    raise NexHealthRateLimitError(
                        f"Rate limit exceeded after {self._max_retries + 1} attempts",
                        retry_after=retry_after,
                    )

                response.raise_for_status()
                payload = response.json()

                # Check NexHealth API error response
                if payload.get("code") is False:
                    errors = payload.get("error", [])
                    error_msg = f"NexHealth API error: {errors}" if errors else "Unknown API error"
                    raise NexHealthAPIError(error_msg, errors=errors)

                return payload

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    continue  # Will be handled above
                # Don't log e.response.text — upstream validation errors can
                # echo back patient-submitted fields. Status + content length
                # are enough for triage; detail belongs in PMS-side logs.
                body_len = len(e.response.content) if e.response.content else 0
                logger.error(
                    "Upstream HTTP error: status=%s body_bytes=%s method=%s path=%s",
                    e.response.status_code, body_len, method, path,
                )
                # TEMP DEBUG (per-user request, demo prep): log both the
                # request payload AND the raw response body for /patients
                # 400 so we can see exactly what NexHealth is rejecting.
                # Remove once diagnosed.
                if (
                    e.response.status_code == 400
                    and method == "POST"
                    and path == "/patients"
                ):
                    raw_body = e.response.text or ""
                    logger.warning(
                        "DEBUG NexHealth /patients 400 REQUEST body: %s",
                        json,
                    )
                    logger.warning(
                        "DEBUG NexHealth /patients 400 RESPONSE body: %s",
                        raw_body[:1000],
                    )
                raise
            except httpx.RequestError as e:
                # Use type name and request URL only — the message can include
                # connection details that aren't useful for log retention.
                logger.error(
                    "Upstream request error: type=%s method=%s path=%s",
                    type(e).__name__, method, path,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                    continue
                raise

        raise RuntimeError("Unexpected retry loop exit")
