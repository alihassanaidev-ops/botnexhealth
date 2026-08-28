"""Celery task: drain the inbound email queue.

Polls SQS, fetches each message from storage, routes it, and acts on the result
— suppress on opt-out, hand off to staff, forward a copy to the clinic.

A queue rather than a webhook, deliberately. Mail waits through a deploy or an
outage instead of being retried at a service that is not listening, and there is
no public endpoint or signature verification to get wrong.

Each queue message is deleted only after its database transaction commits. The
cost of that ordering is an occasional reprocess, which the provider message id
already dedupes; the cost of the other ordering is losing a patient's reply.
"""

from __future__ import annotations

import asyncio
import logging

from src.app.config import settings
from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.worker import celery_app

logger = logging.getLogger(__name__)

#: Kept modest: each message costs an S3 read plus a routing transaction, and
#: the sweep runs often enough that a backlog drains quickly.
_BATCH_SIZE = 10
_MAX_BATCHES = 10


def _ensure_db() -> None:
    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


@celery_app.task(
    name="src.app.tasks.inbound_email.poll_inbound_email",
    bind=True,
    max_retries=2,
    queue="maintenance",
)
def poll_inbound_email(self, *, max_batches: int = _MAX_BATCHES) -> dict:
    if not settings.ses_inbound_queue_url:
        return {"skipped": "no inbound queue configured"}
    _ensure_db()
    try:
        return asyncio.run(_poll_async(max_batches=max_batches))
    except Exception as exc:  # noqa: BLE001 — retried by Celery
        logger.error("inbound email poll failed: %s", exc)
        raise self.retry(exc=exc, countdown=120) from exc


async def _poll_async(*, max_batches: int) -> dict:
    import boto3

    from src.app.services.email.inbound_receiver import InboundMailStore

    sqs = boto3.client("sqs", region_name=settings.ses_region)
    store = InboundMailStore()

    processed = 0
    skipped = 0
    failed = 0

    for _ in range(max_batches):
        response = await asyncio.to_thread(
            sqs.receive_message,
            QueueUrl=settings.ses_inbound_queue_url,
            MaxNumberOfMessages=_BATCH_SIZE,
            WaitTimeSeconds=1,
            VisibilityTimeout=120,
        )
        messages = response.get("Messages") or []
        if not messages:
            break

        for queue_message in messages:
            receipt_handle = queue_message.get("ReceiptHandle")
            try:
                handled = await _handle_one(queue_message, store)
            except Exception as exc:  # noqa: BLE001 — one bad message must not
                # stall the queue behind it; SQS redelivers, then dead-letters.
                failed += 1
                logger.error("inbound email message failed: %s", exc, exc_info=True)
                continue

            if handled:
                processed += 1
            else:
                skipped += 1

            # Delete only after the transaction committed. A reprocess is
            # deduped on the provider message id; a lost reply is not
            # recoverable.
            if receipt_handle:
                await asyncio.to_thread(
                    sqs.delete_message,
                    QueueUrl=settings.ses_inbound_queue_url,
                    ReceiptHandle=receipt_handle,
                )

    logger.info(
        "inbound email poll complete: processed=%d skipped=%d failed=%d",
        processed, skipped, failed,
    )
    return {"processed": processed, "skipped": skipped, "failed": failed}


async def _handle_one(queue_message: dict, store) -> bool:  # noqa: ANN001
    from src.app.services.email.inbound_parser import parse_mime
    from src.app.services.email.inbound_receiver import (
        parse_notification,
        storage_key_for,
    )
    from src.app.services.email.inbound_router import InboundEmailRouter

    notification = parse_notification(queue_message.get("Body") or "")
    if notification is None:
        # Not a receipt notification — a subscription confirmation, or noise.
        # Deleted rather than retried; retrying will not make it parse.
        logger.info("discarding unrecognised inbound queue message")
        return False

    key = storage_key_for(notification)
    raw = await asyncio.to_thread(store.fetch, key) if key else None
    if raw is None:
        logger.warning(
            "inbound message body unavailable: id=%s key=%s",
            notification.message_id, key,
        )
        return False

    parsed = parse_mime(raw)
    # SES lists recipients on the receipt, and a client may strip Reply-To from
    # the headers it echoes — so the routing address is looked for in both.
    parsed.to_addresses = list(
        dict.fromkeys([*parsed.to_addresses, *notification.recipients])
    )

    async with get_system_db_session("celery") as session:
        result = await InboundEmailRouter(session).route(
            parsed,
            provider_message_id=notification.message_id,
            storage_key=key,
            verdicts=notification.verdicts,
        )
        if result is None:
            await session.commit()
            return False

        if result.suppress_email_hash and result.message.institution_id:
            await _suppress(result)

        # The helper dispatches internally, so the run advances within this
        # transaction rather than needing a follow-up task.
        resumed_run_id = await _resume_waiting_run(session, result)
        if resumed_run_id:
            logger.info(
                "inbound email resumed workflow run %s from message %s",
                resumed_run_id, result.message.id,
            )

        if result.needs_staff_attention:
            await _hand_off(session, result)

        await session.commit()

    if result.message.status == "routed":
        # The body is now encrypted in the database; a second plaintext copy in
        # object storage widens the PHI footprint for no benefit.
        await asyncio.to_thread(store.delete, key)

    if result.needs_staff_attention:
        _forward_to_clinic.delay(inbound_message_id=str(result.message.id))

    return True


async def _resume_waiting_run(session, result) -> str | None:  # noqa: ANN001
    """Wake a run parked on an email reply, if this message is that reply.

    The run's timer bounds the window; this shortcuts it when the patient
    actually answers, which is the whole point of waiting rather than guessing.

    Deliberately narrow: only a run that is WAITING on a step whose result code
    is ``awaiting_email_reply``. An auto-responder or a bounce must not count as
    the patient answering, and those never reach here — the router marks them
    and returns before escalation.
    """
    from src.app.models.automation_workflow import (
        AutomationRunStatus,
        AutomationStepStatus,
        AutomationWorkflowRun,
        AutomationWorkflowStepExecution,
    )
    from sqlalchemy import select

    message = result.message
    if not message.workflow_run_id or message.status != "routed":
        return None

    run = await session.get(AutomationWorkflowRun, message.workflow_run_id)
    if run is None or run.status != AutomationRunStatus.WAITING.value:
        return None

    waiting = await session.execute(
        select(AutomationWorkflowStepExecution)
        .where(
            AutomationWorkflowStepExecution.workflow_run_id == run.id,
            AutomationWorkflowStepExecution.status == AutomationStepStatus.WAITING.value,
            AutomationWorkflowStepExecution.result_code == "awaiting_email_reply",
        )
        .limit(1)
    )
    if waiting.scalar_one_or_none() is None:
        return None

    # Reuse the resume helper the SMS path already uses, so both channels wake a
    # run through one code path rather than two that can drift apart.
    from src.app.tasks.automation_workflow import (
        _resume_waiting_run_with_context_updates,
    )

    updates = {
        # A following condition node branches on what the patient actually said.
        "email_reply_message_id": str(message.id),
        "email_reply_intent": message.intent,
    }
    outcome = await _resume_waiting_run_with_context_updates(
        session=session,
        institution_id=str(message.institution_id),
        location_id=str(message.location_id) if message.location_id else "",
        contact_ids=[str(message.contact_id)] if message.contact_id else [],
        workflow_run_id=str(run.id),
        context_updates=updates,
        metadata_updates=updates,
    )
    if outcome.get("resumed"):
        logger.info("inbound email resumed run %s", run.id)
        return str(run.id)
    return None


async def _suppress(result) -> None:  # noqa: ANN001
    """Honour an opt-out by reusing the existing suppression task."""
    from src.app.tasks.email_compliance import suppress_email_consent

    suppress_email_consent.delay(
        institution_id=str(result.message.institution_id),
        email_hash=result.suppress_email_hash,
        reason="inbound_reply_stop",
    )


async def _hand_off(session, result) -> None:  # noqa: ANN001
    """Put the conversation in front of a person.

    Reuses the campaign handoff the SMS path already creates, so the inbox shows
    both channels in one place rather than two parallel queues.
    """
    from src.app.models.campaign_response import CampaignStaffHandoff

    message = result.message
    handoff = CampaignStaffHandoff(
        institution_id=str(message.institution_id),
        location_id=str(message.location_id) if message.location_id else None,
        contact_id=str(message.contact_id) if message.contact_id else None,
        workflow_run_id=str(message.workflow_run_id) if message.workflow_run_id else None,
        conversation_thread_id=(
            str(message.conversation_thread_id) if message.conversation_thread_id else None
        ),
        reason=f"email_{message.intent}",
        status="open",
    )
    session.add(handoff)
    if result.thread is not None:
        result.thread.status = "handoff"


@celery_app.task(
    name="src.app.tasks.inbound_email.forward_to_clinic",
    bind=True,
    max_retries=3,
    queue="maintenance",
)
def _forward_to_clinic(self, *, inbound_message_id: str) -> dict:
    """Send the clinic a copy of a patient reply.

    Re-sent from our own verified domain rather than relayed with the patient's
    address in From. A raw relay fails SPF and DKIM at the clinic's mail
    provider and lands in their spam folder — and keeping the reply-to pointed
    at our routing address means the clinic's answer comes back through the
    inbox instead of going direct and leaving the record incomplete.
    """
    _ensure_db()
    try:
        return asyncio.run(_forward_async(inbound_message_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("forwarding inbound email failed: %s", exc)
        raise self.retry(exc=exc, countdown=180) from exc


async def _forward_async(inbound_message_id: str) -> dict:
    from src.app.models.inbound_email_message import InboundEmailMessage
    from src.app.services.email.identity_service import EmailIdentityService
    from src.app.services.email.sender import EmailMessage
    from src.app.services.automation.email_node_executor import (
        get_patient_email_sender_for,
    )

    async with get_system_db_session("celery") as session:
        message = await session.get(InboundEmailMessage, inbound_message_id)
        if message is None or not message.institution_id:
            return {"skipped": "message not found or unattributed"}

        identity = await EmailIdentityService(session).resolve(
            str(message.institution_id),
            str(message.location_id) if message.location_id else None,
        )
        destination = identity.reply_to
        if not destination or not identity.from_address:
            return {"skipped": "no clinic destination configured"}

        subject = message.subject or "(no subject)"
        body = (
            f"A patient replied to one of your emails.\n\n"
            f"From: {message.from_email_masked or 'unknown'}\n"
            f"{'⚠ Sent from a different address than the patient on file.' if message.sender_mismatch else ''}\n\n"
            f"{message.body or ''}\n\n"
            f"—\nReply to this email and the patient will receive it. "
            f"The conversation is also in your ScaleNexus inbox."
        )

        sender = get_patient_email_sender_for(identity.provider)
        await sender.send(
            EmailMessage(
                from_address=identity.from_address,
                from_name=f"{identity.from_name or 'ScaleNexus'} (patient reply)",
                to=[destination],
                subject=f"Patient reply: {subject}",
                text=body,
                # The clinic's answer comes back through the router, so the
                # thread stays complete instead of continuing off-record.
                reply_to=_router_reply_to(message),
                idempotency_key=f"forward:{message.id}",
                institution_id=str(message.institution_id),
                tenant_name=identity.tenant_name,
                configuration_set=identity.configuration_set,
            )
        )
        await session.commit()
    return {"forwarded": inbound_message_id}


def _router_reply_to(message) -> str | None:  # noqa: ANN001
    from src.app.services.email.reply_address import make_reply_address

    if not settings.ses_inbound_domain:
        return None
    return make_reply_address(
        settings.ses_inbound_domain,
        institution_id=str(message.institution_id),
        location_id=str(message.location_id) if message.location_id else None,
        contact_id=str(message.contact_id) if message.contact_id else None,
        workflow_run_id=str(message.workflow_run_id) if message.workflow_run_id else None,
    )
