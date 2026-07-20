"""Pydantic models for Retell AI function calling and webhooks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================================
# Function Calling Models
# ============================================================================


class FunctionCallRequest(BaseModel):
    """
    Request from Retell AI for function execution.
    
    Retell sends this when the voice agent needs to execute a function
    during a conversation (e.g., check availability, schedule appointment).
    """
    call_id: str | None = Field(None, description="Unique identifier for the call")
    function_name: str | None = Field(None, description="Name of the function to execute")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the function")
    chat: dict[str, Any] | None = None  # Handle alternative payload structure


class FunctionCallResponse(BaseModel):
    """
    Response to Retell AI after function execution.
    
    The agent will use this result to continue the conversation.
    """
    result: dict[str, Any] = Field(..., description="Function execution result")


class FunctionError(BaseModel):
    """Error response for function calls."""
    error: str = Field(..., description="Error message")
    code: str = Field(default="FUNCTION_ERROR", description="Error code")


# ============================================================================
# Webhook Event Models
# ============================================================================


class RetellCallData(BaseModel):
    """Call data included in webhook events.

    Carries Retell's raw (unscrubbed) outputs. The handler selects the raw
    sources at the webhook boundary, falling back to the scrubbed variants only
    when the raw fields are absent.
    """
    call_id: str
    call_type: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    direction: str | None = None
    agent_id: str | None = None
    call_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retell_llm_dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    disconnection_reason: str | None = None
    recording_url: str | None = None  # raw recording URL (scrubbed only as fallback)
    transcript_with_tool_calls: list[dict] | None = None  # raw structured transcript
    # Retell's PII-scrubbed variants, persisted alongside the raw ones so the
    # dashboard can show a non-PII view by default. May be None when Retell
    # redaction is disabled on the account.
    scrubbed_recording_url: str | None = None
    scrubbed_transcript_with_tool_calls: list[dict] | None = None
    scrubbed_summary: str | None = None


class WebhookEvent(BaseModel):
    """
    Webhook event from Retell AI.
    
    Events: call_started, call_ended, call_analyzed
    """
    event: str = Field(..., description="Event type")
    call: RetellCallData = Field(..., description="Call data")


# ============================================================================
# Institutions Response Models (for existing endpoints)
# ============================================================================


class InstitutionResult(BaseModel):
    """Result from get_institutions function."""
    institutions: list[dict[str, Any]]
    count: int
    message: str


class InstitutionDetailResult(BaseModel):
    """Result from get_institution function."""
    institution: dict[str, Any]
    message: str
