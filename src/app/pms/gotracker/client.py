"""HTTP client for the ScaleNexus GoTracker Synchronizer API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class GoTrackerAPIError(RuntimeError):
    """Raised when the GoTracker Synchronizer rejects or cannot serve a request."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoTrackerClient:
    """Small async client around the synchronizer response envelope."""

    def __init__(
        self,
        *,
        base_url: str,
        product_key: str,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._product_key = product_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        response = await self._client.request(
            method,
            url,
            params=dict(params or {}),
            json=json,
            headers={"x-api-key": self._product_key},
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GoTrackerAPIError(
                "GoTracker Synchronizer returned a non-JSON response",
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 400:
            message = _safe_error_message(payload) or response.reason_phrase
            raise GoTrackerAPIError(message, status_code=response.status_code)

        if isinstance(payload, dict) and payload.get("code") is False:
            raise GoTrackerAPIError(
                _safe_error_message(payload) or "GoTracker Synchronizer request failed",
                status_code=response.status_code,
            )

        if not isinstance(payload, dict):
            raise GoTrackerAPIError("GoTracker Synchronizer returned an unexpected payload")

        return payload

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _safe_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, list) and error:
        first = error[0]
        return str(first) if first is not None else None
    code = payload.get("code")
    if isinstance(code, str):
        return code
    description = payload.get("description")
    if isinstance(description, str):
        return description
    return None
