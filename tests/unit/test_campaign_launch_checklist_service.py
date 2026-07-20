"""Unit tests for CampaignLaunchChecklistService (Plan 02)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.launch_checklist_service import (
    CampaignLaunchChecklistService,
)

_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _workflow(definition: dict, *, location_id: str | None = None):
    wf = MagicMock()
    wf.id = "wf-1"
    wf.current_version_id = "ver-1"
    wf.location_id = location_id
    wf.definition = definition
    return wf


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _run(service: CampaignLaunchChecklistService, workflow, **kwargs):
    return asyncio.run(
        service.build(workflow, institution_id="inst-1", **kwargs)
    )


def test_manual_campaign_surfaces_unknown_audience_and_cost() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "Hi", "next_node_id": "x1"},
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
        "compliance": {"content_class": "transactional_care", "consent_required": True},
    }
    session = AsyncMock()
    checklist = _run(CampaignLaunchChecklistService(session), _workflow(definition))

    assert checklist.overall_status == "warning"
    assert checklist.estimated_audience is None
    assert checklist.estimated_send_volume is None
    assert _item(checklist, "audience_estimate").status == "warning"
    assert _item(checklist, "send_volume_cost").status == "unknown"


def test_marketing_without_consent_blocks_launch_checklist() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "Hi", "next_node_id": "x1"},
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
        "compliance": {"content_class": "marketing", "consent_required": False},
    }
    session = AsyncMock()
    checklist = _run(CampaignLaunchChecklistService(session), _workflow(definition))

    assert checklist.overall_status == "blocked"
    assert checklist.blockers_count >= 1
    assert _item(checklist, "compliance_classification").status == "blocked"


def test_appointment_campaign_passes_fresh_nexhealth_check() -> None:
    definition = {
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "entry_node_id": "x1",
        "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
    }
    location = MagicMock(spec=InstitutionLocation)
    location.nexhealth_subdomain = "clinic"
    location.nexhealth_location_id = "loc-ext"
    subscription = MagicMock(spec=NexHealthWebhookSubscription)
    subscription.id = "sub-1"
    subscription.status = NexHealthWebhookSubscriptionStatus.ACTIVE.value
    sync_status = MagicMock()
    sync_status.read_status = "green"
    sync_status.write_status = "green"
    sync_status.last_checked_at = datetime.now(timezone.utc)
    session = AsyncMock()
    session.get = AsyncMock(return_value=location)
    session.execute = AsyncMock(
        side_effect=[_result(subscription), _result(_NOW), _result(sync_status)]
    )

    with patch("src.app.services.automation.launch_checklist_service.datetime") as dt:
        dt.now.return_value = _NOW
        dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        checklist = _run(
            CampaignLaunchChecklistService(session),
            _workflow(definition, location_id="loc-1"),
        )

    assert _item(checklist, "nexhealth_readiness").status == "pass"
    assert _item(checklist, "nexhealth_sync_status").status == "pass"


def test_appointment_campaign_blocks_when_pms_read_sync_is_unhealthy() -> None:
    definition = {
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "entry_node_id": "x1",
        "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
    }
    location = MagicMock(spec=InstitutionLocation)
    location.nexhealth_subdomain = "clinic"
    location.nexhealth_location_id = "loc-ext"
    subscription = MagicMock(spec=NexHealthWebhookSubscription)
    subscription.id = "sub-1"
    subscription.status = NexHealthWebhookSubscriptionStatus.ACTIVE.value
    sync_status = MagicMock()
    sync_status.read_status = "red"
    sync_status.write_status = "green"
    sync_status.last_checked_at = datetime.now(timezone.utc)
    session = AsyncMock()
    session.get = AsyncMock(return_value=location)
    session.execute = AsyncMock(
        side_effect=[_result(subscription), _result(_NOW), _result(sync_status)]
    )

    with patch("src.app.services.automation.launch_checklist_service.datetime") as dt:
        dt.now.return_value = _NOW
        dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        checklist = _run(
            CampaignLaunchChecklistService(session),
            _workflow(definition, location_id="loc-1"),
        )

    assert checklist.overall_status == "blocked"
    assert _item(checklist, "nexhealth_sync_status").status == "blocked"


def test_callback_campaign_surfaces_voice_outcome_and_handoff_readiness() -> None:
    definition = {
        "trigger": {"type": "callback_requested"},
        "entry_node_id": "voice-1",
        "nodes": [
            {
                "type": "send_voice",
                "id": "voice-1",
                "retell_agent_id": "agent-1",
                "wait_for_outcome": True,
                "next_node_id": "condition-1",
            },
            {
                "type": "condition",
                "id": "condition-1",
                "rules": [{"field": "call_outcome", "op": "eq", "value": "booked"}],
                "true_next_node_id": "exit-booked",
                "false_next_node_id": "exit-handoff",
            },
            {"type": "exit", "id": "exit-booked", "outcome": "booked"},
            {"type": "exit", "id": "exit-handoff", "outcome": "staff_handoff"},
        ],
        "compliance": {"content_class": "transactional_care", "consent_required": True},
    }
    session = AsyncMock()

    checklist = _run(CampaignLaunchChecklistService(session), _workflow(definition))

    assert _item(checklist, "callback_queue_source").status == "pass"
    assert _item(checklist, "callback_voice_profile").status == "pass"
    assert _item(checklist, "voice_outcome_wait").status == "pass"
    assert _item(checklist, "callback_staff_fallback").status == "pass"


def _item(checklist, item_id: str):
    return next(item for item in checklist.items if item.id == item_id)
