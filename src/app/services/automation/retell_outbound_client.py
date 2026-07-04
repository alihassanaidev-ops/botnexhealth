"""Thin, mockable HTTP client for placing outbound calls via Retell (Plan 03).

Isolated from the inbound webhook/function code and from the workflow executor so
the vendor call can be mocked in tests and given a single place for timeout/retry
error classification. No PHI in logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_CREATE_CALL_URL = "https://api.retellai.com/v2/create-phone-call"
_TIMEOUT_SECONDS = 15.0


class RetellTransientError(RuntimeError):
    """A recoverable Retell failure (timeout / network / 5xx). The caller should
    retry (Celery task-level) rather than permanently failing the run."""


class RetellPermanentError(RuntimeError):
    """A non-recoverable Retell failure (4xx / bad request / auth). Retrying will
    not help — the caller should fail the run."""


@dataclass(frozen=True)
class RetellCallResult:
    """Result of a successful create-phone-call. ``call_id`` is Retell's unique id
    (V2PhoneCallResponse.call_id) — the correlation key back to the workflow run."""

    call_id: str | None
    call_status: str | None = None


class RetellOutboundClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def create_phone_call(
        self,
        *,
        from_number: str,
        to_number: str,
        override_agent_id: str | None,
        dynamic_variables: dict,
        metadata: dict,
    ) -> RetellCallResult:
        """Place an outbound call. Returns the Retell call_id on success.

        Raises RetellPermanentError on 4xx (don't retry) and RetellTransientError
        on timeout/network/5xx (retryable). Retell exposes no idempotency key on
        this endpoint (confirmed 2026-07-04), so idempotency is the caller's job.
        """
        payload = {
            "from_number": from_number,
            "to_number": to_number,
            "override_agent_id": override_agent_id,
            "retell_llm_dynamic_variables": dynamic_variables,
            "metadata": metadata,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(_CREATE_CALL_URL, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Network-level failure — retryable.
            raise RetellTransientError(f"retell_network_error: {type(exc).__name__}") from exc

        status = response.status_code
        if status >= 500:
            raise RetellTransientError(f"retell_5xx: {status}")
        if status >= 400:
            # 4xx = bad request / auth / not-found — retrying won't help.
            raise RetellPermanentError(f"retell_4xx: {status}")

        call_id: str | None = None
        call_status: str | None = None
        try:
            body = response.json() or {}
            call_id = body.get("call_id")
            call_status = body.get("call_status")
        except Exception:  # noqa: BLE001 — body may not be JSON; call was still placed
            logger.debug("create-phone-call response body was not JSON")
        return RetellCallResult(call_id=call_id, call_status=call_status)
