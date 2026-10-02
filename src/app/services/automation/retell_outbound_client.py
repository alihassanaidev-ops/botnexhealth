"""Thin, mockable HTTP client for placing outbound calls via Retell (Plan 03).

Isolated from the inbound webhook/function code and from the workflow executor so
the vendor call can be mocked in tests and given a single place for timeout/retry
error classification. No PHI in logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.app.services.sms_privacy import sanitize_provider_error

logger = logging.getLogger(__name__)

_CREATE_CALL_URL = "https://api.retellai.com/v2/create-phone-call"
_GET_CALL_URL_TEMPLATE = "https://api.retellai.com/v2/get-call/{call_id}"
_TIMEOUT_SECONDS = 15.0


class RetellTransientError(RuntimeError):
    """A recoverable Retell failure where the call was DEFINITELY NOT placed
    (explicit 5xx server error). Safe to retry (Celery task-level)."""


class RetellPermanentError(RuntimeError):
    """A non-recoverable Retell failure (4xx / bad request / auth). Retrying will
    not help — the caller should fail the run."""


class RetellAmbiguousError(RuntimeError):
    """A timeout / network failure where we CANNOT know whether Retell placed the
    call (the request may have reached Retell but the response was lost). Retell has
    no idempotency key (A-4), so retrying risks double-dialing the patient. Per the
    at-most-once rule (XC-1b, option A) the caller must NOT retry — it fails the run
    and leaves the P9 claim blocking so a redelivery can't re-dial either."""


@dataclass(frozen=True)
class RetellCallResult:
    """Result of a successful create-phone-call. ``call_id`` is Retell's unique id
    (V2PhoneCallResponse.call_id) — the correlation key back to the workflow run."""

    call_id: str | None
    call_status: str | None = None


@dataclass(frozen=True)
class RetellCallDetails:
    """Result of Retell's get-call endpoint for outcome reconciliation."""

    call_id: str | None
    call_status: str | None = None
    disconnection_reason: str | None = None
    call_analysis: dict | None = None
    scrubbed_call_analysis: dict | None = None
    metadata: dict | None = None
    scrubbed_metadata: dict | None = None
    retell_llm_dynamic_variables: dict | None = None
    scrubbed_retell_llm_dynamic_variables: dict | None = None


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

        Error classification (XC-1b, option A — Retell has no idempotency key):
          * 4xx            → RetellPermanentError  (bad request/auth; not placed)
          * 5xx            → RetellTransientError   (server rejected; not placed → retry)
          * timeout/network→ RetellAmbiguousError   (may have been placed → do NOT retry)
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
            # Network-level failure — the request MAY have reached Retell (a call may
            # have been placed) but the response was lost. Ambiguous → do NOT retry.
            raise RetellAmbiguousError(f"retell_network_error: {type(exc).__name__}") from exc

        status = response.status_code
        if status >= 500:
            # Explicit server error — the call was NOT placed. Safe to retry.
            raise RetellTransientError(f"retell_5xx: {status}")
        if status >= 400:
            # 4xx = bad request / auth / not-found — retrying won't help.
            detail = sanitize_provider_error(response.text, max_length=180)
            raise RetellPermanentError(f"retell_4xx: {status}: {detail}")

        call_id: str | None = None
        call_status: str | None = None
        try:
            body = response.json() or {}
            call_id = body.get("call_id")
            call_status = body.get("call_status")
        except Exception:  # noqa: BLE001 — body may not be JSON; call was still placed
            logger.debug("create-phone-call response body was not JSON")
        return RetellCallResult(call_id=call_id, call_status=call_status)

    async def get_phone_call(self, call_id: str) -> RetellCallDetails:
        """Fetch a Retell call by id.

        Used as a fallback when Retell's final webhook is delayed or missed. This
        is read-only, so timeout/network failures are treated as transient and the
        poller can try again later without risking a second patient call.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    _GET_CALL_URL_TEMPLATE.format(call_id=call_id),
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetellTransientError(f"retell_get_network_error: {type(exc).__name__}") from exc

        status = response.status_code
        if status >= 500:
            raise RetellTransientError(f"retell_get_5xx: {status}")
        if status >= 400:
            raise RetellPermanentError(f"retell_get_4xx: {status}")

        try:
            body = response.json() or {}
        except Exception:  # noqa: BLE001 — invalid JSON is a provider contract error
            raise RetellPermanentError("retell_get_invalid_json") from None

        return RetellCallDetails(
            call_id=body.get("call_id") or call_id,
            call_status=body.get("call_status"),
            disconnection_reason=body.get("disconnection_reason"),
            call_analysis=body.get("call_analysis"),
            scrubbed_call_analysis=body.get("scrubbed_call_analysis"),
            metadata=body.get("metadata"),
            scrubbed_metadata=body.get("scrubbed_metadata"),
            retell_llm_dynamic_variables=body.get("retell_llm_dynamic_variables"),
            scrubbed_retell_llm_dynamic_variables=body.get(
                "scrubbed_retell_llm_dynamic_variables"
            ),
        )
