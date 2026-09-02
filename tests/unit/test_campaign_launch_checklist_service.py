"""Unit tests for CampaignLaunchChecklistService (Plan 02)."""

from __future__ import annotations

import pytest

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.gotracker_webhook_subscription import (
    GoTrackerWebhookSubscription,
    GoTrackerWebhookSubscriptionStatus,
)
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.services.automation.launch_checklist_service import (
    CampaignLaunchChecklistService,
    _pms_capability_requirements,
)
from src.app.services.automation.definition_schema import WorkflowDefinition

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
    return asyncio.run(service.build(workflow, institution_id="inst-1", **kwargs))


def test_manual_campaign_surfaces_unknown_audience_and_volume() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Hi",
                "next_node_id": "x1",
            },
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
    volume_item = _item(checklist, "send_volume_cost")
    assert volume_item.status == "unknown"
    assert volume_item.label == "Estimated send volume"
    assert "cost" not in volume_item.message.lower()
    assert "spend" not in volume_item.message.lower()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Compliance enforcement is disabled in validation_service.validate() — the `issues += self._consent_and_content(definition)` line is commented out with 'managed by Retell for now'. The rule itself still exists and is correct. strict=True so that re-enabling it turns this into a failure and forces a deliberate revisit rather than leaving a silently-skipped compliance test."
    ),
)
def test_marketing_without_consent_blocks_launch_checklist() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Hi",
                "next_node_id": "x1",
            },
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


def test_appointment_campaign_passes_fresh_gotracker_check() -> None:
    definition = {
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "entry_node_id": "x1",
        "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
    }
    location = MagicMock(spec=InstitutionLocation)
    location.nexhealth_subdomain = None
    location.nexhealth_location_id = None
    location.gotracker_product_key_encrypted = "encrypted-key"
    location.gotracker_base_url = "https://gotracker.example"
    subscription = MagicMock(spec=GoTrackerWebhookSubscription)
    subscription.id = "gt-sub-1"
    subscription.status = GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    subscription.last_event_at = _NOW
    subscription.event_types = ["appointment.created", "appointment.updated"]
    session = AsyncMock()
    session.get = AsyncMock(return_value=location)
    session.execute = AsyncMock(side_effect=[_result(subscription), _result(_NOW)])

    with patch("src.app.services.automation.launch_checklist_service.datetime") as dt:
        dt.now.return_value = _NOW
        dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        checklist = _run(
            CampaignLaunchChecklistService(session),
            _workflow(definition, location_id="loc-1"),
        )

    assert _item(checklist, "gotracker_readiness").status == "pass"
    assert all(item.id != "nexhealth_readiness" for item in checklist.items)


def test_recall_campaign_blocks_gotracker_when_history_sync_is_incomplete() -> None:
    definition = {
        "trigger": {"type": "recall_scan", "recall_interval_months": 6},
        "entry_node_id": "x1",
        "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
        "pms_context_fields": [
            "recall_type_name",
            "has_active_treatment_plan",
        ],
    }
    institution = MagicMock(spec=Institution)
    institution.id = "inst-1"
    institution.slug = "clinic"
    institution.pms_type = "gotracker"
    location = MagicMock(spec=InstitutionLocation)
    location.id = "loc-1"
    location.slug = "downtown"
    location.nexhealth_subdomain = None
    location.nexhealth_location_id = None
    location.gotracker_product_key_encrypted = "encrypted-key"
    location.gotracker_base_url = "https://gotracker.example"
    subscription = MagicMock(spec=GoTrackerWebhookSubscription)
    subscription.id = "gt-sub-1"
    subscription.status = GoTrackerWebhookSubscriptionStatus.ACTIVE.value
    subscription.last_event_at = _NOW
    subscription.event_types = ["appointment.created", "appointment.updated"]
    adapter = MagicMock()
    adapter.source = "gotracker"
    adapter.get_recall_history_sync_status = AsyncMock(
        return_value={
            "appointment_history": {"status": "running", "progress_percent": 40}
        }
    )
    adapter.close = AsyncMock()

    def get_model(model, _id):
        if model is Institution:
            return institution
        return location

    session = AsyncMock()
    session.get = AsyncMock(side_effect=get_model)
    session.execute = AsyncMock(side_effect=[_result(subscription), _result(_NOW)])

    with patch(
        "src.app.pms.factory.get_adapter_for_institution_location",
        new=AsyncMock(return_value=adapter),
    ):
        checklist = _run(
            CampaignLaunchChecklistService(session),
            _workflow(definition, location_id="loc-1"),
        )

    item = _item(checklist, "gotracker_recall_history")
    assert item.status == "blocked"
    assert item.metadata["reason"] == "history_sync_incomplete"
    assert checklist.overall_status == "blocked"


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


def test_treatment_campaign_blocks_when_pms_lacks_treatment_plans() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "x1",
        "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
    }
    workflow = _workflow(definition, location_id="loc-1")
    workflow.category = "treatment"
    institution = MagicMock()
    institution.id = "inst-1"
    institution.pms_type = "nexhealth"
    location = MagicMock(spec=InstitutionLocation)
    location.id = "loc-1"
    sync_status = MagicMock()
    sync_status.sync_source_name = "Dentrix Ascend"
    sync_status.sync_source_type = None
    sync_status.emr_payload = {"display_name": "Dentrix Ascend"}
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[institution, location])
    session.execute = AsyncMock(side_effect=[_result(sync_status), _result(None)])

    checklist = _run(CampaignLaunchChecklistService(session), workflow)

    assert checklist.overall_status == "blocked"
    item = _item(checklist, "pms_capability")
    assert item.status == "blocked"
    assert item.metadata["missing"] == ["treatment_plans"]


def test_checklist_derives_pms_requirements_from_context_fields() -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "entry_node_id": "x1",
            "nodes": [{"type": "exit", "id": "x1", "outcome": "done"}],
            "pms_context_fields": ["has_active_treatment_plan"],
        }
    )

    assert _pms_capability_requirements(_workflow({}), definition) == [
        "treatment_plans"
    ]


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
