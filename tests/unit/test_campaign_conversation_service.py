"""Run-scoped campaign SMS conversation service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.app.models.automation_workflow import AutomationWorkflowRun, AutomationWorkflowVersion
from src.app.services.automation.campaign_conversation_service import (
    CampaignConversationService,
)


def _result(*, scalars_all=None, scalar_one_or_none=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = scalars_all or []
    result.scalar_one_or_none.return_value = scalar_one_or_none
    return result


def _definition(*, response_window_seconds=259200):
    return {
        "trigger": {"type": "manual"},
        "entry_node_id": "sms-1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "Reply DONE",
                "next_node_id": "wait-1",
                "response_window_seconds": response_window_seconds,
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


def _explicit_wait_definition():
    return {
        "trigger": {"type": "manual"},
        "entry_node_id": "sms-1",
        "nodes": [
            {
                "type": "send_sms",
                "id": "sms-1",
                "body_template": "Reply YES or NO",
                "next_node_id": "wait-sms-1",
                "response_mappings": [
                    {
                        "tokens": ["YES"],
                        "context_updates": {"legacy_reply": "yes"},
                    }
                ],
            },
            {
                "type": "wait",
                "id": "wait-sms-1",
                "next_node_id": "exit-1",
                "wait_for": {
                    "type": "sms_reply",
                    "response_window_seconds": 3600,
                    "response_mappings": [
                        {
                            "tokens": ["YES"],
                            "context_updates": {"sms_reply": "yes"},
                        }
                    ],
                },
            },
            {"type": "exit", "id": "exit-1"},
        ],
    }


def _thread(**over):
    thread = MagicMock()
    thread.id = "thread-1"
    thread.contact_id = "contact-1"
    thread.workflow_run_id = "run-1"
    thread.status = "open"
    thread.last_message_at = datetime.now(timezone.utc)
    thread.opened_at = thread.last_message_at
    for key, value in over.items():
        setattr(thread, key, value)
    return thread


def _run(status="waiting"):
    run = MagicMock(spec=AutomationWorkflowRun)
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_id = "wf-1"
    run.workflow_version_id = "ver-1"
    run.current_step_id = "wait-1"
    run.status = status
    return run


def _version(response_window_seconds=259200):
    version = MagicMock(spec=AutomationWorkflowVersion)
    version.definition = _definition(response_window_seconds=response_window_seconds)
    return version


def _explicit_wait_version():
    version = MagicMock(spec=AutomationWorkflowVersion)
    version.definition = _explicit_wait_definition()
    return version


def _session(*, run=None, version=None, execute_results=None):
    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results or [])

    async def _get(model, pk, **_kwargs):
        if model is AutomationWorkflowRun:
            return run
        if model is AutomationWorkflowVersion:
            return version
        return None

    session.get = AsyncMock(side_effect=_get)
    return session


def test_contact_thread_resolves_when_response_window_is_open():
    thread = _thread()
    session = _session(
        run=_run(),
        version=_version(),
        execute_results=[
            _result(scalars_all=[thread]),
            _result(scalar_one_or_none="sms-1"),
        ],
    )

    resolved = asyncio.run(
        CampaignConversationService(session).resolve_sms_thread(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
        )
    )

    assert resolved is thread


def test_expired_response_window_does_not_resolve_thread():
    thread = _thread(
        last_message_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session = _session(
        run=_run(),
        version=_version(response_window_seconds=60),
        execute_results=[
            _result(scalars_all=[thread]),
            _result(scalar_one_or_none="sms-1"),
        ],
    )

    resolved = asyncio.run(
        CampaignConversationService(session).resolve_sms_thread(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
        )
    )

    assert resolved is None
    assert thread.status == "completed"
    assert thread.completion_reason == "response_window_expired"


def test_terminal_handoff_thread_does_not_make_waiting_run_ambiguous():
    cancelled_thread = _thread(
        id="thread-cancelled",
        workflow_run_id="run-cancelled",
        status="handoff",
    )
    waiting_thread = _thread(
        id="thread-waiting",
        workflow_run_id="run-waiting",
    )
    cancelled_run = _run(status="cancelled")
    cancelled_run.id = "run-cancelled"
    waiting_run = _run(status="waiting")
    waiting_run.id = "run-waiting"

    session = _session(
        execute_results=[
            _result(scalars_all=[cancelled_thread, waiting_thread]),
            _result(scalar_one_or_none="sms-1"),
            _result(scalar_one_or_none="sms-1"),
        ],
    )

    async def _get(model, pk, **_kwargs):
        if model is AutomationWorkflowRun:
            return {
                "run-cancelled": cancelled_run,
                "run-waiting": waiting_run,
            }.get(pk)
        if model is AutomationWorkflowVersion:
            return _version()
        return None

    session.get = AsyncMock(side_effect=_get)

    resolved = asyncio.run(
        CampaignConversationService(session).resolve_sms_thread(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
        )
    )

    assert resolved is waiting_thread


def test_open_sms_thread_reuses_handoff_thread_as_active():
    thread = _thread(status="handoff")
    session = _session(execute_results=[_result(scalar_one_or_none=thread)])

    resolved = asyncio.run(
        CampaignConversationService(session).open_sms_thread(
            _run(status="running"),
        )
    )

    assert resolved is thread
    session.add.assert_not_called()


def test_response_mapping_prefers_current_sms_reply_wait_node():
    run = _run()
    run.current_step_id = "wait-sms-1"
    session = _session(
        run=run,
        version=_explicit_wait_version(),
    )

    match = asyncio.run(
        CampaignConversationService(session).match_sms_response_mapping(
            workflow_run_id="run-1",
            body="yes",
        )
    )

    assert match is not None
    assert match.node_id == "wait-sms-1"
    assert match.mapping.context_updates == {"sms_reply": "yes"}


def test_terminal_run_closes_thread_without_unresolved_handoff():
    thread = _thread()
    run = _run(status="completed")
    session = _session(
        execute_results=[
            _result(scalars_all=[thread]),
            _result(scalar_one_or_none=None),
        ]
    )

    asyncio.run(
        CampaignConversationService(session).close_terminal_threads_for_run(
            run,
            completion_reason="workflow_completed",
        )
    )

    assert thread.status == "completed"
    assert thread.completion_reason == "workflow_completed"
