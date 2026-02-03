"""Pydantic models for GoHighLevel API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GHLCustomField(BaseModel):
    """Custom field for GHL contact."""
    id: str
    field_value: str


class GHLContactUpsert(BaseModel):
    """Request body for GHL contact upsert."""
    locationId: str
    phone: str
    name: str | None = None
    dateOfBirth: str | None = None
    email: str | None = None
    customFields: list[GHLCustomField] = Field(default_factory=list)


class GHLContactResponse(BaseModel):
    """Response from GHL contact upsert."""
    contact: dict[str, Any] | None = None
    new: bool | None = None
    traceId: str | None = None
