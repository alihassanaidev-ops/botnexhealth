"""Background processing for Retell-generated SMS conversation turns."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.app.config import settings
from src.app.database import get_system_db_session, init_database, is_database_initialized
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationWorkflowVersion,
)
from src.app.models.contact import Contact
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.institution_location import InstitutionLocation
from src.app.models.retell_sms import (
    ACTIVE_RETELL_SMS_SESSION_STATUSES,
    RetellSmsSession,
    RetellSmsSessionStatus,
    RetellSmsTurnStatus,
)
from src.app.models.sms_history_log import SmsStatus
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.services.automation.compliance_gate_service import ComplianceGateService
from src.app.services.automation.definition_schema import (
    RetellSmsConversationNode,
    WorkflowDefinition,
)
from src.app.services.automation.revalidation import PmsLiveRevalidationService
from src.app.services.automation.retell_chat_client import (
    RetellChatAmbiguousError,
    RetellChatClient,
    RetellChatPermanentError,
    RetellChatTransientError,
)
from src.app.services.automation.retell_sms_conversation_service import (
    RetellSmsConversationConfigurationError,
    RetellSmsConversationService,
    agent_response_text,
)
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.step_dispatcher import build_dispatcher
from src.app.services.sms_service import SmsService
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


def _ensure_db() -> None:
    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


@celery_app.task(
    name="src.app.tasks.retell_sms.process_retell_sms_turn",
    queue="workflow",
)
def process_retell_sms_turn(
    *,
    institution_id: str,
    location_id: str,
    session_id: str,
    inbound_sms_message_id: str,
) -> dict:
    _ensure_db()
    return asyncio.run(
        _process_turn_async(
            institution_id=institution_id,
            location_id=location_id,
            session_id=session_id,
            inbound_sms_message_id=inbound_sms_message_id,
        )
    )


async def _process_turn_async(
    *,
    institution_id: str,
    location_id: str,
    session_id: str,
    inbound_sms_message_id: str,
) -> dict:
    now = datetime.now(timezone.utc)
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=f"retell_sms_turn:{session_id}",
    ) as db:
        retell_session = await db.get(RetellSmsSession, session_id)
        inbound = await db.get(InboundSmsMessage, inbound_sms_message_id)
        if retell_session is None or inbound is None:
            return {"processed": False, "reason": "session_or_inbound_not_found"}
        if (
            str(retell_session.institution_id) != institution_id
            or str(retell_session.location_id) != location_id
        ):
            return {"processed": False, "reason": "tenant_scope_mismatch"}

        run = await db.get(AutomationWorkflowRun, retell_session.workflow_run_id)
        version = (
            await db.get(AutomationWorkflowVersion, retell_session.workflow_version_id)
            if run is not None
            else None
        )
        if run is None or version is None:
            return {"processed": False, "reason": "workflow_state_not_found"}
        definition = WorkflowDefinition.model_validate(version.definition)
        node = next(
            (
                item
                for item in definition.nodes
                if item.id == retell_session.step_id
                and isinstance(item, RetellSmsConversationNode)
            ),
            None,
        )
        if node is None:
            return {"processed": False, "reason": "conversation_node_not_found"}

        if retell_session.status not in ACTIVE_RETELL_SMS_SESSION_STATUSES:
            return {"processed": False, "reason": "session_terminal"}
        if now >= retell_session.expires_at or now >= retell_session.max_expires_at:
            service = RetellSmsConversationService(db)
            service.mark_terminal(
                retell_session,
                status=RetellSmsSessionStatus.TIMED_OUT.value,
                outcome="timeout",
                now=now,
            )
            extra: dict[str, str] = {}
            if node.timeout_behavior == "handoff":
                handoff = await service.create_handoff(
                    retell_session,
                    reason="automation_failed",
                    summary=(
                        "A patient reply reached an expired Retell SMS conversation "
                        "and needs staff follow-up."
                    ),
                    inbound=inbound,
                )
                extra["retell_sms_handoff_id"] = str(handoff.id)
            await _resume_terminal(
                db,
                run,
                definition,
                node,
                retell_session,
                extra_context=extra,
            )
            await db.commit()
            return {"processed": False, "reason": "session_expired"}

        if node.respect_quiet_hours:
            gate = await ComplianceGateService(db).check(
                run,
                "send_sms",
                now=now,
                content_class=(
                    definition.compliance.content_class if definition.compliance else None
                ),
            )
            if gate.action == "hold":
                retry_at = gate.retry_at or (now + timedelta(hours=1))
                retell_session.last_activity_at = now
                retell_session.expires_at = min(
                    retry_at + timedelta(seconds=node.inactivity_timeout_seconds),
                    retell_session.max_expires_at,
                )
                scheduler = AutomationWorkflowSchedulerService(db)
                await scheduler.cancel_timers_for_run(str(run.id))
                await scheduler.create_timer(
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id),
                    workflow_run_id=str(run.id),
                    step_execution_id=str(retell_session.step_execution_id),
                    due_at=retell_session.expires_at,
                    timezone_name="UTC",
                )
                await db.commit()
                process_retell_sms_turn.apply_async(
                    kwargs={
                        "institution_id": institution_id,
                        "location_id": location_id,
                        "session_id": session_id,
                        "inbound_sms_message_id": inbound_sms_message_id,
                    },
                    eta=retry_at,
                )
                return {
                    "processed": False,
                    "reason": "quiet_hours",
                    "retry_at": retry_at.isoformat(),
                }

        service = RetellSmsConversationService(db)
        retell_session, turn, should_process = await service.claim_turn(
            session_id=session_id,
            inbound=inbound,
            now=now,
        )
        if not should_process:
            if turn is None:
                process_retell_sms_turn.apply_async(
                    kwargs={
                        "institution_id": institution_id,
                        "location_id": location_id,
                        "session_id": session_id,
                        "inbound_sms_message_id": inbound_sms_message_id,
                    },
                    countdown=2,
                )
                return {"processed": False, "reason": "turn_queued_behind_in_flight"}
            return {"processed": False, "reason": "duplicate_turn"}
        assert turn is not None
        # Receiving a valid turn resets inactivity immediately. This makes a
        # timer/worker race favor the patient message while still respecting the
        # hard maximum session lifetime.
        retell_session.expires_at = min(
            now + timedelta(seconds=node.inactivity_timeout_seconds),
            retell_session.max_expires_at,
        )
        await db.commit()

        if service.is_handoff_requested(inbound.body, node):
            service = RetellSmsConversationService(db)
            retell_session = await db.get(RetellSmsSession, session_id)
            turn = await db.get(type(turn), turn.id)
            assert retell_session is not None and turn is not None
            service.mark_terminal(
                retell_session,
                status=RetellSmsSessionStatus.HANDOFF.value,
                outcome="patient_requested_handoff",
            )
            handoff = await service.create_handoff(
                retell_session,
                reason="patient_asks_for_staff",
                summary="Patient asked for a staff member during the AI SMS conversation.",
                inbound=inbound,
            )
            turn.status = RetellSmsTurnStatus.COMPLETED.value
            turn.completed_at = datetime.now(timezone.utc)
            await _resume_terminal(
                db,
                run,
                definition,
                node,
                retell_session,
                extra_context={"retell_sms_handoff_id": str(handoff.id)},
            )
            await db.commit()
            await _best_effort_end_chat(retell_session)
            return {"processed": True, "outcome": "patient_requested_handoff"}

        try:
            client = RetellChatClient(settings.retell_api_secret or "")
            context = dict(run.trigger_metadata or {})
            if not retell_session.retell_chat_id:
                dynamic_variables = await service.dynamic_variables(
                    retell_session=retell_session,
                    node=node,
                    context=context,
                )
                created = await client.create_chat(
                    agent_id=retell_session.retell_agent_id,
                    agent_version=retell_session.agent_version,
                    dynamic_variables=dynamic_variables,
                    metadata={
                        "institution_id": str(retell_session.institution_id),
                        "location_id": str(retell_session.location_id),
                        "workflow_run_id": str(retell_session.workflow_run_id),
                        "retell_sms_session_id": str(retell_session.id),
                    },
                )
                retell_session.retell_chat_id = created.chat_id
                await db.commit()

            messages = await client.create_completion(
                chat_id=str(retell_session.retell_chat_id),
                content=inbound.body or "",
            )
            response_text, message_ids = agent_response_text(
                messages, max_segments=node.max_response_segments
            )
            if not response_text:
                raise RetellChatPermanentError("retell_completion_empty_agent_response")

            contact = await db.get(Contact, retell_session.contact_id)
            location = await db.get(InstitutionLocation, retell_session.location_id)
            if contact is None or not contact.phone or location is None or not location.twilio_from_number:
                raise RetellSmsConversationConfigurationError("SMS endpoint unavailable")

            has_prior_outbound = (
                await db.execute(
                    select(SmsHistoryLog.id)
                    .where(
                        SmsHistoryLog.conversation_thread_id
                        == str(retell_session.conversation_thread_id)
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

            sms_log = await SmsService(db).send_sms(
                from_number=location.twilio_from_number,
                to_number=contact.phone,
                body=response_text,
                institution_location_id=str(location.id),
                patient_contact_id=str(contact.id),
                workflow_run_id=str(run.id),
                workflow_id=str(run.workflow_id),
                conversation_thread_id=str(retell_session.conversation_thread_id),
                include_opt_out_footer=has_prior_outbound is None,
            )
            if sms_log.status not in {SmsStatus.SENT.value, SmsStatus.DELIVERED.value}:
                raise RetellSmsConversationConfigurationError(
                    f"twilio_reply_{sms_log.status}"
                )

            terminal = await service.finish_turn(
                retell_session=retell_session,
                turn=turn,
                outbound_sms_history_id=str(sms_log.id),
                retell_message_ids=message_ids,
                node=node,
            )
            await db.commit()
            chat_ended = False
            try:
                details = await client.get_chat(str(retell_session.retell_chat_id))
                chat_ended = (details.status or "").lower() in {"ended", "error"}
            except RetellChatTransientError:
                logger.info("Retell chat status read deferred session=%s", session_id)
            except RetellChatPermanentError:
                # The response is already delivered. Do not replay it just because
                # Retell can no longer return the chat record.
                chat_ended = True

            if chat_ended and not terminal:
                service.mark_terminal(
                    retell_session,
                    status=RetellSmsSessionStatus.COMPLETED.value,
                    outcome="retell_chat_ended",
                )
                terminal = True

            if terminal:
                await _resume_terminal(db, run, definition, node, retell_session)
            else:
                scheduler = AutomationWorkflowSchedulerService(db)
                await scheduler.cancel_timers_for_run(str(run.id))
                await scheduler.create_timer(
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id),
                    workflow_run_id=str(run.id),
                    step_execution_id=str(retell_session.step_execution_id),
                    due_at=retell_session.expires_at,
                    timezone_name=location.timezone or "UTC",
                )
            await db.commit()
            if terminal:
                await _best_effort_end_chat(retell_session)
            return {
                "processed": True,
                "terminal": terminal,
                "turn_count": retell_session.turn_count,
            }
        except (
            RetellChatAmbiguousError,
            RetellChatPermanentError,
            RetellSmsConversationConfigurationError,
        ) as exc:
            await db.rollback()
            retell_session = await db.get(RetellSmsSession, session_id)
            turn = await db.get(type(turn), turn.id)
            run = await db.get(AutomationWorkflowRun, retell_session.workflow_run_id) if retell_session else None
            if retell_session is None or turn is None or run is None:
                raise
            service = RetellSmsConversationService(db)
            turn.status = (
                RetellSmsTurnStatus.AMBIGUOUS.value
                if isinstance(exc, RetellChatAmbiguousError)
                else RetellSmsTurnStatus.FAILED.value
            )
            turn.failure_code = type(exc).__name__
            turn.error_message = type(exc).__name__
            await _handle_failure(
                db, service, run, definition, node, retell_session, type(exc).__name__
            )
            await db.commit()
            return {"processed": False, "reason": type(exc).__name__}


async def _handle_failure(
    db,
    service: RetellSmsConversationService,
    run: AutomationWorkflowRun,
    definition: WorkflowDefinition,
    node: RetellSmsConversationNode,
    retell_session: RetellSmsSession,
    failure_code: str,
) -> None:
    service.mark_terminal(
        retell_session,
        status=RetellSmsSessionStatus.FAILED.value,
        outcome="failed",
        failure_code=failure_code,
    )
    if node.failure_behavior == "fail":
        scheduler = AutomationWorkflowSchedulerService(db)
        await scheduler.cancel_timers_for_run(str(run.id))
        runtime = AutomationWorkflowRuntimeService(db)
        step = await db.get(
            AutomationWorkflowStepExecution, retell_session.step_execution_id
        )
        if step is not None:
            await runtime.fail_step(step, result_code="retell_sms_failed")
        await runtime.fail_run(run, reason="retell_sms_failed")
        return

    extra: dict[str, str] = {}
    if node.failure_behavior == "handoff":
        handoff = await service.create_handoff(
            retell_session,
            reason="automation_failed",
            summary="The AI SMS conversation could not continue and needs staff follow-up.",
        )
        extra["retell_sms_handoff_id"] = str(handoff.id)
    await _resume_terminal(db, run, definition, node, retell_session, extra_context=extra)


async def _resume_terminal(
    db,
    run: AutomationWorkflowRun,
    definition: WorkflowDefinition,
    node: RetellSmsConversationNode,
    retell_session: RetellSmsSession,
    *,
    extra_context: dict | None = None,
) -> None:
    """Advance past a terminal conversation without re-entering the parked node."""
    if run.status != AutomationRunStatus.WAITING.value:
        return
    step = await db.get(AutomationWorkflowStepExecution, retell_session.step_execution_id)
    if step is None:
        return
    scheduler = AutomationWorkflowSchedulerService(db)
    await scheduler.cancel_timers_for_run(str(run.id))
    context = RetellSmsConversationService.result_context(retell_session)
    context.update(extra_context or {})
    metadata = dict(run.trigger_metadata or {})
    metadata.update(context)
    run.trigger_metadata = metadata
    step.result_code = f"retell_sms_{retell_session.terminal_outcome or retell_session.status}"
    step.result_metadata = context
    runtime = AutomationWorkflowRuntimeService(db)
    await runtime.resume_run(run, step)
    run.current_step_id = node.next_node_id
    dispatcher, location_timezone = await build_dispatcher(
        db,
        location_id=str(run.location_id) if run.location_id else None,
        runtime=runtime,
        scheduler=scheduler,
        revalidator=PmsLiveRevalidationService(db),
    )
    await dispatcher.advance(
        run,
        definition,
        context=metadata,
        location_timezone=location_timezone,
    )


async def _best_effort_end_chat(retell_session: RetellSmsSession) -> None:
    if not retell_session.retell_chat_id or not settings.retell_api_secret:
        return
    try:
        await RetellChatClient(settings.retell_api_secret).end_chat(
            str(retell_session.retell_chat_id)
        )
    except Exception:  # noqa: BLE001 - local terminal state remains authoritative
        logger.warning(
            "Could not end terminal Retell chat session=%s",
            retell_session.id,
            exc_info=True,
        )


@celery_app.task(
    name="src.app.tasks.retell_sms.end_retell_sms_chat",
    queue="workflow",
)
def end_retell_sms_chat(
    *, institution_id: str, location_id: str, session_id: str
) -> dict:
    _ensure_db()
    return asyncio.run(
        _end_chat_async(
            institution_id=institution_id,
            location_id=location_id,
            session_id=session_id,
        )
    )


async def _end_chat_async(
    *, institution_id: str, location_id: str, session_id: str
) -> dict:
    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        location_id=location_id,
        external_id=f"retell_sms_end:{session_id}",
    ) as db:
        retell_session = await db.get(RetellSmsSession, session_id)
        if retell_session is None or not retell_session.retell_chat_id:
            return {"ended": False, "reason": "chat_not_created"}
        await _best_effort_end_chat(retell_session)
        return {"ended": True}
