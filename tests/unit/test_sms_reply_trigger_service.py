"""Unit tests for inbound SMS reply trigger matching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.app.services.automation.sms_reply_trigger_service import (
    SmsReplyTriggerService,
    sms_reply_idempotency_key,
    workflow_matches_sms_reply,
)


def _workflow(*, location_id="loc-1", tokens=None, trigger=None):
    # `sms_reply` is now the SMS channel of the merged inbound_message trigger.
    trigger = trigger or {
        "type": "inbound_message",
        "channels": ["sms"],
        "tokens": tokens or [],
    }
    wf = MagicMock()
    wf.id = "wf-1"
    wf.location_id = location_id
    wf.current_version_id = "ver-1"
    wf.definition = {
        "triggers": [trigger],
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1"}],
    }
    # The shared lookup reads these model properties; a MagicMock cannot derive
    # them from `definition` the way AutomationWorkflow does.
    wf.trigger_type = trigger["type"]
    wf.trigger_types = [trigger["type"]]
    wf.subscribed_event_keys = list(trigger.get("event_keys") or [])
    return wf


def _result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def test_workflow_matches_sms_reply_uses_whole_token_filters() -> None:
    workflow = _workflow(tokens=["price"])

    assert workflow_matches_sms_reply(workflow, "What is the price?")
    assert not workflow_matches_sms_reply(workflow, "surprised")


def test_find_active_sms_reply_workflows_honors_location_scope() -> None:
    matching = _workflow(location_id="loc-1")
    other_location = _workflow(location_id="loc-2")
    institution_wide = _workflow(location_id=None)
    non_sms = _workflow(location_id="loc-1", trigger={"type": "manual"})
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_result([matching, other_location, institution_wide, non_sms])
    )

    workflows = asyncio.run(
        SmsReplyTriggerService(session).find_active_sms_reply_workflows(
            "inst-1",
            location_id="loc-1",
        )
    )

    assert workflows == [matching, institution_wide]


def test_sms_reply_idempotency_key_is_version_and_message_scoped() -> None:
    assert sms_reply_idempotency_key("ver-1", "inbound-1") == "sms-reply:ver-1:inbound-1"
