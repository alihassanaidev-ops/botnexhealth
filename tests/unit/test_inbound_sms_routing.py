"""Unit tests for S-2 inbound SMS routing (persistence + correlation)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.automation_workflow import (
    AutomationWorkflowRun,
    AutomationWorkflowVersion,
)
from src.app.services.automation.inbound_sms_routing_service import (
    InboundSmsRoutingService,
)


def _session(*, contact_ids=None):
    """Session whose execute() call returns contact ids."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    contact_result = MagicMock()
    contact_result.scalars.return_value.all.return_value = contact_ids or []
    session.execute = AsyncMock(return_value=contact_result)
    return session


def _thread(**over):
    thread = MagicMock()
    thread.id = "thread-1"
    thread.contact_id = "c-1"
    thread.workflow_run_id = "r-1"
    thread.last_message_at = datetime.now(timezone.utc)
    thread.opened_at = thread.last_message_at
    for key, value in over.items():
        setattr(thread, key, value)
    return thread


def _record(session, *, thread=None, **over):
    svc = InboundSmsRoutingService(session)
    kw = dict(
        institution_id="inst-1",
        location_id="loc-1",
        from_number="+14165551234",
        to_number="+15005550000",
        body="I need to move my appointment",
        intent="free_text",
        message_sid="SM123",
    )
    kw.update(over)
    with patch(
        "src.app.services.automation.inbound_sms_routing_service.CampaignConversationService"
    ) as MockThreads:
        instance = MockThreads.return_value
        instance.resolve_sms_thread = AsyncMock(return_value=thread)
        instance.mark_message_seen = AsyncMock()
        msg = asyncio.run(svc.record_inbound(**kw))
    return msg


def test_persists_row_with_hashed_masked_phones_and_encrypted_body():
    session = _session(contact_ids=["c-1"])
    msg = _record(session)
    session.add.assert_called_once()
    session.flush.assert_awaited()
    assert msg.intent == "free_text"
    assert msg.from_phone_hash and msg.from_phone_hash != "+14165551234"
    assert msg.from_phone_masked and msg.from_phone_masked.endswith("1234")
    # body stored encrypted, readable via the property
    assert msg.body_encrypted is not None
    assert msg.body_encrypted != "I need to move my appointment"
    assert msg.body == "I need to move my appointment"


def test_correlates_contact_and_run_when_unambiguous():
    session = _session(contact_ids=["c-1"])
    msg = _record(session, thread=_thread())
    assert msg.contact_id == "c-1"
    assert msg.workflow_run_id == "r-1"
    assert msg.conversation_thread_id == "thread-1"


def test_shared_phone_multiple_contacts_stays_uncorrelated():
    session = _session(contact_ids=["c-1", "c-2"])
    msg = _record(session)
    assert msg.contact_id is None
    assert msg.workflow_run_id is None


def test_shared_phone_correlates_when_one_reply_eligible_thread_matches():
    thread = _thread(contact_id="c-2", workflow_run_id="r-2")
    run = MagicMock(spec=AutomationWorkflowRun)
    run.id = "r-2"
    run.status = "waiting"
    run.workflow_version_id = "v-1"
    run.current_step_id = "wait-1"
    version = MagicMock(spec=AutomationWorkflowVersion)
    version.definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "sms-1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "Reply YES",
                "next_node_id": "wait-1",
            },
            {
                "type": "wait",
                "id": "wait-1",
                "delay": {"delay_type": "duration", "duration_seconds": 3600},
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }

    contacts_result = MagicMock()
    contacts_result.scalars.return_value.all.return_value = ["c-1", "c-2"]
    threads_result = MagicMock()
    threads_result.scalars.return_value.all.return_value = [thread]
    step_result = MagicMock()
    step_result.scalar_one_or_none.return_value = "sms-1"
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[contacts_result, threads_result, step_result]
    )

    async def _get(model, _pk, **_kwargs):
        if model is AutomationWorkflowRun:
            return run
        if model is AutomationWorkflowVersion:
            return version
        return None

    session.get = AsyncMock(side_effect=_get)

    msg = asyncio.run(
        InboundSmsRoutingService(session).record_inbound(
            institution_id="inst-1",
            location_id="loc-1",
            from_number="+14165551234",
            to_number="+15005550000",
            body="YES",
            intent="confirm",
            message_sid="SM-shared-phone",
        )
    )

    assert msg.contact_id == "c-2"
    assert msg.workflow_run_id == "r-2"
    assert msg.conversation_thread_id == "thread-1"


def test_multiple_active_threads_leaves_run_null():
    session = _session(contact_ids=["c-1"])
    msg = _record(session)
    assert msg.contact_id == "c-1"
    assert msg.workflow_run_id is None  # ambiguous → staff notified instead


def test_no_contact_match_leaves_both_null():
    session = _session(contact_ids=[])
    msg = _record(session)
    assert msg.contact_id is None
    assert msg.workflow_run_id is None


def test_intent_is_preserved():
    session = _session(contact_ids=["c-1"])
    msg = _record(session, intent="stop")
    assert msg.intent == "stop"
