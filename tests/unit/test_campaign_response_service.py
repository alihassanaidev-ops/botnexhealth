"""Campaign response event and handoff recording."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.services.automation.campaign_response_service import CampaignResponseService
from src.app.services.automation.sms_intent_parser import parse_sms_intent


def _result(value=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _session(*, run=None):
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=run)
    session.execute = AsyncMock(return_value=_result(None))
    return session


def _inbound(intent="free_text"):
    msg = InboundSmsMessage(
        id="inbound-1",
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        workflow_run_id="run-1",
        message_sid="SM123",
        intent=intent,
        from_phone_masked="***1234",
        to_phone_masked="***0000",
    )
    msg.body = "cancel my appointment"
    return msg


def _run():
    return AutomationWorkflowRun(
        id="run-1",
        institution_id="inst-1",
        location_id="loc-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        contact_id="contact-1",
        status="waiting",
        trigger_metadata={"source": "test"},
    )


def test_sms_cancel_request_creates_response_event_and_handoff():
    run = _run()
    session = _session(run=run)

    event, handoff = asyncio.run(
        CampaignResponseService(session).record_sms_response(
            _inbound("cancel_requested"),
            body="cancel my appointment",
            parsed=parse_sms_intent("cancel my appointment"),
        )
    )

    assert event.channel == "sms"
    assert event.normalized_intent == "cancel_requested"
    assert event.normalized_outcome == "staff_handoff_required"
    assert event.workflow_id == "wf-1"
    assert event.raw_body == "cancel my appointment"
    assert handoff is not None
    assert handoff.reason == "cancel_requested"
    assert handoff.status == "open"
    assert run.trigger_metadata["patient_response_intent"] == "cancel_requested"
    assert run.trigger_metadata["last_campaign_response_event_id"] == event.id
    assert session.add.call_count == 2


def test_sms_confirmation_records_event_without_handoff():
    run = _run()
    session = _session(run=run)

    event, handoff = asyncio.run(
        CampaignResponseService(session).record_sms_response(
            _inbound("confirm"),
            body="YES",
            parsed=parse_sms_intent("YES"),
        )
    )

    assert event.normalized_intent == "confirm"
    assert event.normalized_outcome == "confirmed_by_reply"
    assert handoff is None
    assert run.trigger_metadata["patient_response_outcome"] == "confirmed_by_reply"
    assert session.add.call_count == 1


def test_voice_unknown_outcome_creates_handoff():
    run = _run()
    session = _session(run=run)
    attempt = MagicMock()
    attempt.location_id = "loc-1"
    attempt.workflow_run_id = "run-1"
    session.execute.side_effect = [_result(None), _result(attempt)]

    event, handoff = asyncio.run(
        CampaignResponseService(session).record_voice_response(
            institution_id="inst-1",
            retell_call_id="call-1",
            call_outcome="unknown",
            disconnection_reason=None,
        )
    )

    assert event.channel == "voice"
    assert event.normalized_outcome == "unknown"
    assert event.workflow_run_id == "run-1"
    assert handoff is not None
    assert handoff.reason == "ambiguous_voice_outcome"
