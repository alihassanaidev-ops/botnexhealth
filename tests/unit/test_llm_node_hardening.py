"""AI Action hardening: retry classification and the patient-context allowlist."""

from __future__ import annotations

import httpx
import pytest

from src.app.services.automation.definition_schema import LlmNode
from src.app.services.automation.llm_node_executor import (
    WorkflowLlmError,
    _call_with_retries,
    _is_transient,
    _permitted_context,
    _request_payload,
)


def _node(**overrides) -> LlmNode:
    payload = {
        "type": "llm",
        "id": "ai",
        "source_field": "appointment_reason",
        "output_field": "intent",
        "prompt_template": "Classify the reason.",
        "next_node_id": "n-next",
    }
    payload.update(overrides)
    return LlmNode.model_validate(payload)


_CONTEXT = {
    "appointment_reason": "tooth pain",
    "patient": {"first_name": "Ada", "date_of_birth": "1980-04-01"},
    "phone_number": "+15550100",
}


# ---------------------------------------------------------------------------
# Context allowlist
# ---------------------------------------------------------------------------


def test_no_allowlist_sends_the_whole_context() -> None:
    """What every definition published before the field existed meant."""
    node = _node(include_context=True)
    assert _permitted_context(node, _CONTEXT) == _CONTEXT


def test_allowlist_sends_only_the_named_fields() -> None:
    node = _node(include_context=True, context_fields=["appointment_reason"])
    assert _permitted_context(node, _CONTEXT) == {"appointment_reason": "tooth pain"}


def test_allowlist_resolves_dotted_paths() -> None:
    node = _node(include_context=True, context_fields=["patient.first_name"])
    assert _permitted_context(node, _CONTEXT) == {"patient.first_name": "Ada"}


def test_allowlist_omits_absent_fields_rather_than_sending_null() -> None:
    node = _node(include_context=True, context_fields=["not_in_context"])
    assert _permitted_context(node, _CONTEXT) == {}


def test_context_is_absent_entirely_when_the_toggle_is_off() -> None:
    """The default. Nothing beyond the prompt and the source field leaves."""
    payload = _request_payload(_node(include_context=False), _CONTEXT, "some-model")
    sent = payload["input"][1]["content"]
    assert "workflow_context" not in sent
    assert "1980-04-01" not in sent


def test_an_allowlisted_node_does_not_leak_the_rest_of_the_record() -> None:
    node = _node(include_context=True, context_fields=["appointment_reason"])
    sent = _request_payload(node, _CONTEXT, "some-model")["input"][1]["content"]
    assert "tooth pain" in sent
    assert "1980-04-01" not in sent
    assert "+15550100" not in sent


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_provider_trouble_is_retried(status: int) -> None:
    assert _is_transient(_status_error(status)) is True


def test_timeouts_and_transport_errors_are_retried() -> None:
    assert _is_transient(httpx.ReadTimeout("slow")) is True
    assert _is_transient(httpx.ConnectError("refused")) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_our_own_mistakes_are_not_retried(status: int) -> None:
    """A bad request fails identically three times — retrying only adds latency."""
    assert _is_transient(_status_error(status)) is False


def test_unparseable_output_is_not_retried() -> None:
    assert _is_transient(WorkflowLlmError("LLM output was not valid JSON")) is False


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_and_recorded(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_call(node, context):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(503)
        return "confirmed", {"provider": "openai"}

    monkeypatch.setattr(
        "src.app.services.automation.llm_node_executor._call_openai", fake_call
    )
    monkeypatch.setattr(
        "src.app.services.automation.llm_node_executor.settings.workflow_llm_max_attempts",
        3,
    )
    monkeypatch.setattr(
        "src.app.services.automation.llm_node_executor.settings"
        ".workflow_llm_retry_base_delay_seconds",
        0,
    )

    value, metadata = await _call_with_retries(_node(), _CONTEXT)
    assert value == "confirmed"
    assert calls["n"] == 3
    # The trace says it took three goes, so a flaky provider is visible rather
    # than hidden behind a successful-looking step.
    assert metadata["attempts"] == 3


@pytest.mark.asyncio
async def test_a_permanent_failure_gives_up_immediately(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_call(node, context):
        calls["n"] += 1
        raise _status_error(400)

    monkeypatch.setattr(
        "src.app.services.automation.llm_node_executor._call_openai", fake_call
    )
    monkeypatch.setattr(
        "src.app.services.automation.llm_node_executor.settings.workflow_llm_max_attempts",
        3,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _call_with_retries(_node(), _CONTEXT)
    assert calls["n"] == 1
