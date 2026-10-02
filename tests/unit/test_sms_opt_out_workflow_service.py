"""SMS STOP must terminate only the workflow runs participating in SMS conversation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowRun,
)
from src.app.services.automation.sms_opt_out_workflow_service import (
    SmsOptOutWorkflowService,
)


def _result(runs: list[AutomationWorkflowRun]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = runs
    return result


def _run(run_id: str) -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        id=run_id,
        institution_id="inst-1",
        location_id="loc-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        contact_id="contact-1",
        status=AutomationRunStatus.WAITING.value,
    )


def test_correlated_stop_cancels_exact_active_run_timer_and_sms_thread() -> None:
    run = _run("run-1")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result([run]))
    scheduler = AsyncMock()
    enrollment = AsyncMock()

    with (
        patch(
            "src.app.services.automation.sms_opt_out_workflow_service."
            "AutomationWorkflowSchedulerService",
            return_value=scheduler,
        ),
        patch(
            "src.app.services.automation.sms_opt_out_workflow_service."
            "AutomationWorkflowEnrollmentService",
            return_value=enrollment,
        ),
    ):
        cancelled = asyncio.run(
            SmsOptOutWorkflowService(session).cancel_active_sms_runs(
                institution_id="inst-1",
                location_id="loc-1",
                phone="+14165550100",
                correlated_run_id="run-1",
            )
        )

    assert cancelled == 1
    scheduler.cancel_timers_for_run.assert_awaited_once_with("run-1")
    enrollment.cancel_run.assert_awaited_once_with(
        run,
        reason="sms_opt_out",
        sms_completion_reason="sms_opt_out",
        preserve_unresolved_sms_handoffs=False,
        require_sms_thread_close=True,
    )
    query = str(session.execute.await_args.args[0])
    assert "automation_workflow_runs.id" in query
    assert "campaign_conversation_threads" in query
    assert "contacts" not in query


def test_ambiguous_shared_phone_cancels_every_active_sms_thread_at_location_only() -> (
    None
):
    runs = [_run("run-1"), _run("run-2")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(runs))
    scheduler = AsyncMock()
    enrollment = AsyncMock()

    with (
        patch(
            "src.app.services.automation.sms_opt_out_workflow_service."
            "AutomationWorkflowSchedulerService",
            return_value=scheduler,
        ),
        patch(
            "src.app.services.automation.sms_opt_out_workflow_service."
            "AutomationWorkflowEnrollmentService",
            return_value=enrollment,
        ),
    ):
        cancelled = asyncio.run(
            SmsOptOutWorkflowService(session).cancel_active_sms_runs(
                institution_id="inst-1",
                location_id="loc-1",
                phone="+14165550100",
                correlated_run_id=None,
            )
        )

    assert cancelled == 2
    assert scheduler.cancel_timers_for_run.await_count == 2
    assert enrollment.cancel_run.await_count == 2
    query = str(session.execute.await_args.args[0])
    assert "JOIN campaign_conversation_threads" in query
    assert "JOIN contacts" in query
    assert "contacts.phone_hash" in query
    assert "campaign_conversation_threads.location_id" in query
    assert "campaign_conversation_threads.channel" in query
    assert "campaign_conversation_threads.status" in query
    assert "automation_workflow_runs.status" in query
