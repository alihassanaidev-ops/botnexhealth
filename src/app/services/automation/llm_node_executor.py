"""Executor helper for workflow LLM nodes.

The dispatcher owns graph movement; this module owns the external model call and
the small amount of output shaping needed to make downstream Condition nodes
deterministic.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.app.config import settings
from src.app.services.automation.definition_schema import LlmNode
from src.app.services.automation.step_dispatcher import (
    _assign_context_value,
    _classify_with_label_rules,
    _context_value,
    _metadata_value,
)
from src.app.services.automation.template_renderer import render_sms_body


class WorkflowLlmError(RuntimeError):
    """Raised when an LLM node cannot produce a usable output."""


@dataclass
class LlmExecutionResult:
    value: object
    metadata: dict[str, object]


def _is_transient(exc: Exception) -> bool:
    """Whether retrying the same request could plausibly succeed.

    Timeouts, connection errors, 429s and 5xxs are the provider having a bad
    moment. A 4xx that is not a rate limit, or output that failed to parse
    against the node's own schema, will fail identically on every attempt — and
    retrying those turns one failed run into three times the latency before the
    same failure.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


async def _call_with_retries(
    node: LlmNode, context: dict
) -> tuple[object, dict[str, object]]:
    """Call the model, retrying transient provider failures with backoff."""
    attempts = max(1, settings.workflow_llm_max_attempts)
    base_delay = max(0.0, settings.workflow_llm_retry_base_delay_seconds)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            value, metadata = await _call_openai(node, context)
        except Exception as exc:  # noqa: BLE001 - normalized at workflow boundary
            last_error = exc
            if attempt >= attempts or not _is_transient(exc):
                raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
            continue
        if attempt > 1:
            metadata["attempts"] = attempt
        return value, metadata
    raise last_error or WorkflowLlmError("LLM call failed")


async def execute_llm_node(node: LlmNode, context: dict) -> LlmExecutionResult:
    """Run a workflow LLM node and write its output into ``context``."""
    try:
        value, metadata = await _call_with_retries(node, context)
    except Exception as exc:  # noqa: BLE001 - normalized at workflow boundary
        allow_fallback = (
            node.allow_keyword_fallback
            if node.allow_keyword_fallback is not None
            else settings.workflow_llm_allow_keyword_fallback
        )
        allow_fallback = allow_fallback or (
            not node.require_model and node.output_mode == "label"
        )
        if not allow_fallback:
            raise WorkflowLlmError(_safe_error_message(exc)) from exc
        value = _classify_with_label_rules(node, _context_value(context, node.source_field))
        metadata = {
            "provider": "keyword_fallback",
            "source_field": node.source_field,
            "output_field": node.output_field,
            "label": value,
            "fallback_reason": _safe_error_message(exc),
        }

    _write_output(node, context, value)
    return LlmExecutionResult(value=value, metadata=metadata)


async def _call_openai(node: LlmNode, context: dict) -> tuple[object, dict[str, object]]:
    api_key = settings.openai_api_key
    if not api_key:
        raise WorkflowLlmError("OPENAI_API_KEY is not configured")

    model = node.model or settings.workflow_llm_default_model
    payload = _request_payload(node, context, model)
    base_url = settings.openai_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.workflow_llm_timeout_seconds) as client:
        response = await client.post(
            f"{base_url}/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
    body = response.json()
    text = _response_text(body)
    value = _parse_output(node, text)
    metadata: dict[str, object] = {
        "provider": "openai",
        "model": model,
        "response_id": body.get("id"),
        "source_field": node.source_field,
        "output_field": node.output_field,
        "output_mode": node.output_mode,
        "value": _metadata_value(value),
    }
    usage = body.get("usage")
    if isinstance(usage, dict):
        metadata["usage"] = _metadata_value(usage)
    return value, metadata


def _request_payload(node: LlmNode, context: dict, model: str) -> dict[str, Any]:
    source_value = _context_value(context, node.source_field)
    rendered_prompt = render_sms_body(node.prompt_template, None, None, context)
    input_context: dict[str, object] = {
        "source_field": node.source_field,
        "source_value": _metadata_value(source_value),
        "output_field": node.output_field,
        "output_mode": node.output_mode,
    }
    if node.labels:
        input_context["allowed_labels"] = node.labels
    if node.include_context:
        input_context["workflow_context"] = _metadata_value(
            _permitted_context(node, context)
        )

    instructions = (
        "You are a workflow AI action. Follow the user's instruction and return "
        "only the requested output. Do not include explanations."
    )
    if node.output_mode == "label":
        instructions += " Return a single JSON object with a string property named value."
    elif node.output_mode == "text":
        instructions += " Return a single JSON object with a string property named value."
    else:
        instructions += " Return valid JSON matching the requested fields."

    payload: dict[str, Any] = {
        "model": model,
        "store": False,
        "max_output_tokens": node.max_output_tokens,
        "input": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": (
                    f"{rendered_prompt}\n\n"
                    f"Workflow input:\n{json.dumps(input_context, ensure_ascii=True)}"
                ),
            },
        ],
    }
    text_format = _text_format(node)
    if text_format is not None:
        payload["text"] = {"format": text_format}
    return payload


def _permitted_context(node: LlmNode, context: dict) -> dict:
    """The subset of the run context this node is allowed to send.

    An empty ``context_fields`` means the whole context, which is what every
    definition published before the field existed meant and what they must keep
    meaning. Anything else is an explicit allowlist, resolved through the same
    dotted-path lookup the rest of the node uses so ``patient.first_name`` works
    as well as a top-level key. A named field that is absent from the context is
    simply omitted rather than sent as null.
    """
    if not node.context_fields:
        return context
    permitted: dict = {}
    for field_name in node.context_fields:
        value = _context_value(context, field_name)
        if value is not None:
            permitted[field_name] = value
    return permitted


def _text_format(node: LlmNode) -> dict[str, Any] | None:
    if node.output_mode in {"label", "text"}:
        value_schema: dict[str, Any] = {"type": "string"}
        if node.output_mode == "label" and node.labels:
            value_schema["enum"] = node.labels
        return {
            "type": "json_schema",
            "name": "workflow_llm_output",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": value_schema},
            },
        }
    if node.json_schema:
        return {
            "type": "json_schema",
            "name": "workflow_llm_output",
            "strict": False,
            "schema": node.json_schema,
        }
    return None


def _response_text(body: dict[str, Any]) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text

    chunks: list[str] = []
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    text = "".join(chunks).strip()
    if not text:
        raise WorkflowLlmError("OpenAI response did not contain output text")
    return text


def _parse_output(node: LlmNode, text: str) -> object:
    if node.output_mode == "json":
        return _loads_json(text)

    raw = _loads_json(text)
    if isinstance(raw, dict):
        value = raw.get("value")
    else:
        value = raw
    if value in (None, ""):
        raise WorkflowLlmError("LLM output did not include a value")
    if node.output_mode == "label" and node.labels and str(value) not in node.labels:
        raise WorkflowLlmError("LLM output label is not in the allowed label list")
    return str(value)


def _loads_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise WorkflowLlmError("LLM output was not valid JSON")
        return json.loads(match.group(0))


def _write_output(node: LlmNode, context: dict, value: object) -> None:
    _assign_context_value(context, node.output_field, value)
    if node.output_mode == "json" and isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key:
                _assign_context_value(context, key, item)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"OpenAI request failed with status {exc.response.status_code}"
    message = str(exc).strip()
    return message[:240] if message else exc.__class__.__name__
