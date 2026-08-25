"""Small Retell Chat API adapter used by the SMS conversation module."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.app.services.sms_privacy import sanitize_provider_error

_BASE_URL = "https://api.retellai.com"
_TIMEOUT_SECONDS = 20.0


class RetellChatPermanentError(RuntimeError):
    """A request Retell rejected and retrying unchanged will not fix."""


class RetellChatTransientError(RuntimeError):
    """A read-only Retell request can be retried safely."""


class RetellChatAmbiguousError(RuntimeError):
    """A mutating request may have succeeded; never replay it automatically."""


@dataclass(frozen=True)
class RetellChatMessage:
    role: str
    content: str
    message_id: str | None = None


@dataclass(frozen=True)
class RetellChatDetails:
    chat_id: str
    status: str | None
    messages: tuple[RetellChatMessage, ...] = ()


class RetellChatClient:
    """Owns Retell endpoint details and conservative failure classification."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RetellChatPermanentError("retell_api_key_missing")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def create_chat(
        self,
        *,
        agent_id: str,
        agent_version: int | None,
        dynamic_variables: dict[str, str],
        metadata: dict[str, str],
    ) -> RetellChatDetails:
        payload: dict[str, object] = {
            "agent_id": agent_id,
            "retell_llm_dynamic_variables": dynamic_variables,
            "metadata": metadata,
        }
        if agent_version is not None:
            payload["agent_version"] = agent_version
        body = await self._mutating_request("POST", "/create-chat", payload)
        chat_id = str(body.get("chat_id") or "").strip()
        if not chat_id:
            raise RetellChatPermanentError("retell_create_chat_missing_chat_id")
        return _chat_details(body, fallback_chat_id=chat_id)

    async def create_completion(
        self, *, chat_id: str, content: str
    ) -> tuple[RetellChatMessage, ...]:
        body = await self._mutating_request(
            "POST",
            "/create-chat-completion",
            {"chat_id": chat_id, "content": content},
        )
        messages = _messages(body.get("messages"))
        if not messages:
            raise RetellChatPermanentError("retell_completion_missing_messages")
        return messages

    async def get_chat(self, chat_id: str) -> RetellChatDetails:
        body = await self._read_request("GET", f"/get-chat/{chat_id}")
        return _chat_details(body, fallback_chat_id=chat_id)

    async def end_chat(self, chat_id: str) -> RetellChatDetails:
        body = await self._mutating_request("PATCH", f"/end-chat/{chat_id}", {})
        return _chat_details(body, fallback_chat_id=chat_id)

    async def _mutating_request(
        self, method: str, path: str, payload: dict[str, object]
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.request(
                    method, f"{_BASE_URL}{path}", headers=self._headers, json=payload
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetellChatAmbiguousError(
                f"retell_chat_network_error:{type(exc).__name__}"
            ) from exc
        return self._decode(response, mutating=True)

    async def _read_request(self, method: str, path: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.request(
                    method, f"{_BASE_URL}{path}", headers=self._headers
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetellChatTransientError(
                f"retell_chat_read_network_error:{type(exc).__name__}"
            ) from exc
        return self._decode(response, mutating=False)

    @staticmethod
    def _decode(response: httpx.Response, *, mutating: bool) -> dict:
        if response.status_code >= 500:
            error_type = RetellChatAmbiguousError if mutating else RetellChatTransientError
            raise error_type(f"retell_chat_5xx:{response.status_code}")
        if response.status_code >= 400:
            detail = sanitize_provider_error(response.text, max_length=180)
            raise RetellChatPermanentError(
                f"retell_chat_4xx:{response.status_code}:{detail}"
            )
        try:
            body = response.json() or {}
        except Exception as exc:  # noqa: BLE001 - provider contract violation
            error_type = RetellChatAmbiguousError if mutating else RetellChatPermanentError
            raise error_type("retell_chat_invalid_json") from exc
        if not isinstance(body, dict):
            raise RetellChatPermanentError("retell_chat_invalid_response_shape")
        return body


def _messages(value: object) -> tuple[RetellChatMessage, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[RetellChatMessage] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role and content:
            parsed.append(
                RetellChatMessage(
                    role=role,
                    content=content,
                    message_id=(str(item["message_id"]) if item.get("message_id") else None),
                )
            )
    return tuple(parsed)


def _chat_details(body: dict, *, fallback_chat_id: str) -> RetellChatDetails:
    return RetellChatDetails(
        chat_id=str(body.get("chat_id") or fallback_chat_id),
        status=str(body["chat_status"]) if body.get("chat_status") else None,
        messages=_messages(body.get("messages")),
    )

