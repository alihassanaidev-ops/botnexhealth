"""Location scoping for every trigger service.

Regression coverage for the cross-location enrollment defect: before this, only
the SMS reply path consulted ``AutomationWorkflow.location_id``, so an event at
one location could enroll a workflow bound to a different location in the same
institution. The run then carried the *event's* location, so the patient was
contacted with the wrong clinic's voice profile, sending number and hours.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.automation_workflow import AutomationWorkflowStatus
from src.app.services.automation.appointment_trigger_service import (
    AppointmentTriggerService,
)
from src.app.services.automation.callback_trigger_service import CallbackTriggerService
from src.app.services.automation.enquiry_trigger_service import EnquiryTriggerService
from src.app.services.automation.patient_status_trigger_service import (
    PatientStatusTriggerService,
)
from src.app.services.automation.sms_reply_trigger_service import SmsReplyTriggerService
from src.app.services.automation.trigger_lookup import (
    TRIGGER_EVENT_KEYS,
    workflow_matches_location,
)


#: Each service's lookup key paired with the trigger a campaign now authors for
#: it. The dispatch tasks still ask by the retired key names, so the bridge in
#: ``trigger_lookup.TRIGGER_EVENT_KEYS`` is what has to resolve them — which is
#: only exercised if the fixtures carry the *new* trigger shapes.
_TRIGGERS: dict[str, dict] = {
    "appointment_offset": {
        "type": "event",
        "event_keys": ["appointment.reminder_due"],
        "reminder_offset_hours": -24,
    },
    "appointment_state_changed": {
        "type": "event",
        "event_keys": ["appointment.completed", "appointment.cancelled"],
    },
    "recall_scan": {
        "type": "schedule",
        "cron": "0 9 * * *",
        "source": {"kind": "pms_recall", "recall_interval_months": 6},
    },
    "callback_requested": {
        "type": "event",
        "event_keys": ["call.inbound.completed"],
    },
    "patient_status_changed": {
        "type": "internal_status",
        "field": "patient_workflow_status",
        "to_statuses": ["appointment_confirmed"],
    },
    "sms_reply": {"type": "inbound_message", "channels": ["sms"]},
    "enquiry_received": {"type": "event", "event_keys": ["enquiry.received"]},
    "manual": {"type": "manual"},
}


def _workflow(*, trigger_type: str, location_id: str | None, wf_id: str = "wf"):
    trigger = _TRIGGERS[trigger_type]
    wf = MagicMock()
    wf.id = wf_id
    wf.institution_id = "inst-1"
    wf.location_id = location_id
    wf.status = AutomationWorkflowStatus.ACTIVE.value
    wf.current_version_id = "ver-1"
    wf.definition = {
        "triggers": [trigger],
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1"}],
    }
    # Mirrors AutomationWorkflow's derived properties, which a MagicMock cannot
    # compute from `definition` on its own.
    wf.trigger_type = trigger["type"]
    wf.trigger_types = [trigger["type"]]
    wf.subscribed_event_keys = list(trigger.get("event_keys") or []) + [
        key
        for key in TRIGGER_EVENT_KEYS.get(trigger["type"], ())
        if key not in (trigger.get("event_keys") or [])
    ]
    return wf


def _session(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_unscoped_workflow_matches_any_location() -> None:
    wf = _workflow(trigger_type="manual", location_id=None)
    assert workflow_matches_location(wf, "loc-1") is True
    assert workflow_matches_location(wf, None) is True


def test_scoped_workflow_matches_only_its_own_location() -> None:
    wf = _workflow(trigger_type="manual", location_id="loc-1")
    assert workflow_matches_location(wf, "loc-1") is True
    assert workflow_matches_location(wf, "loc-2") is False


def test_scoped_workflow_does_not_match_unknown_location() -> None:
    """An event we cannot place must not enroll a location-bound workflow."""
    wf = _workflow(trigger_type="manual", location_id="loc-1")
    assert workflow_matches_location(wf, None) is False


def test_location_comparison_is_string_based() -> None:
    """UUID columns and string task kwargs must compare equal."""
    wf = _workflow(trigger_type="manual", location_id=None)
    wf.location_id = MagicMock()
    wf.location_id.__str__ = lambda self: "loc-1"  # type: ignore[method-assign]
    assert workflow_matches_location(wf, "loc-1") is True


# ---------------------------------------------------------------------------
# Every trigger service honours it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trigger_type", "call"),
    [
        (
            "appointment_offset",
            lambda svc, loc: AppointmentTriggerService(
                svc
            ).find_active_appointment_workflows("inst-1", location_id=loc),
        ),
        (
            "appointment_state_changed",
            lambda svc, loc: AppointmentTriggerService(
                svc
            ).find_active_appointment_state_workflows("inst-1", location_id=loc),
        ),
        (
            "recall_scan",
            lambda svc, loc: AppointmentTriggerService(
                svc
            ).find_active_recall_workflows("inst-1", location_id=loc),
        ),
        (
            "callback_requested",
            lambda svc, loc: CallbackTriggerService(svc).find_active_callback_workflows(
                "inst-1", location_id=loc
            ),
        ),
        (
            "patient_status_changed",
            lambda svc, loc: PatientStatusTriggerService(
                svc
            ).find_active_status_workflows("inst-1", location_id=loc),
        ),
        (
            "sms_reply",
            lambda svc, loc: SmsReplyTriggerService(
                svc
            ).find_active_sms_reply_workflows("inst-1", location_id=loc),
        ),
        (
            "enquiry_received",
            lambda svc, loc: EnquiryTriggerService(svc).find_active_enquiry_workflows(
                "inst-1", location_id=loc
            ),
        ),
    ],
)
def test_trigger_service_excludes_other_locations(trigger_type, call) -> None:
    here = _workflow(trigger_type=trigger_type, location_id="loc-1", wf_id="here")
    elsewhere = _workflow(trigger_type=trigger_type, location_id="loc-2", wf_id="away")
    everywhere = _workflow(trigger_type=trigger_type, location_id=None, wf_id="all")
    other_trigger = _workflow(trigger_type="manual", location_id="loc-1", wf_id="other")

    session = _session([here, elsewhere, everywhere, other_trigger])
    matched = asyncio.run(call(session, "loc-1"))

    assert [wf.id for wf in matched] == ["here", "all"]
