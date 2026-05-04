"""Unit tests for the Redis-backed SSE event bus."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.app.services import event_bus
from src.app.services.event_bus import SUPPORTED_EVENT_TYPES, publish_event


def test_supported_event_types_frozen() -> None:
    assert SUPPORTED_EVENT_TYPES == frozenset(
        {"calls_updated", "callbacks_updated", "dashboard_updated", "notification"}
    )


def test_publish_event_requires_institution_id() -> None:
    with pytest.raises(ValueError, match="institution_id is required"):
        publish_event("", "calls_updated")


def test_publish_event_rejects_unknown_event_type() -> None:
    with pytest.raises(ValueError, match="Unsupported SSE event type"):
        publish_event("inst-1", "made_up_event")


_VALID_PAYLOADS: dict[str, dict] = {
    "calls_updated": {},
    "callbacks_updated": {},
    "dashboard_updated": {},
    "notification": {
        "notification_id": "n-1",
        "title": "New call",
        "severity": "info",
    },
}


@pytest.mark.parametrize("event_type", sorted(SUPPORTED_EVENT_TYPES))
def test_publish_event_publishes_to_institution_channel(event_type: str) -> None:
    client = MagicMock()
    payload_in = _VALID_PAYLOADS[event_type]
    with patch.object(event_bus, "_get_sync_client", return_value=client):
        publish_event("inst-abc", event_type, payload_in)

    client.publish.assert_called_once()
    channel, payload_json = client.publish.call_args.args
    assert channel == "sse:institution:inst-abc"

    import json
    payload = json.loads(payload_json)
    assert payload["type"] == event_type
    assert payload["data"] == payload_in
    assert "timestamp" in payload


def test_publish_event_rejects_payload_not_matching_schema() -> None:
    """Schema validation refuses unknown fields on system-update events."""
    with pytest.raises(ValueError, match="Invalid SSE event payload"):
        publish_event("inst-1", "calls_updated", {"unexpected_field": "x"})


def test_publish_event_notification_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="Invalid SSE event payload"):
        publish_event(
            "inst-1",
            "notification",
            {"notification_id": "n-1", "patient_phone": "+15551234567"},
        )
