"""Feed provider delivery results back into the campaign that sent the message.

Twilio reports twice for a message: once when it accepts it, and again when it
reaches (or fails to reach) the handset. Only the first of those had ever
touched the campaign, so a message the carrier later dropped still counted as a
successful contact — the patient was never reached and the reporting said they
were.

The step execution records the provider's message id in ``result_metadata`` when
it sends, which is what lets a receipt arriving minutes later find the attempt it
belongs to without a schema change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.app.database import get_system_db_session
from src.app.models.automation_workflow import AutomationWorkflowStepExecution

logger = logging.getLogger(__name__)

#: Provider statuses meaning the handset genuinely received the message.
DELIVERED_STATUSES = frozenset({"delivered"})
#: Terminal failures: the message will not arrive, however long we wait.
UNDELIVERED_STATUSES = frozenset({"failed", "undelivered"})


def classify_receipt(provider_status: str) -> str | None:
    """Map a provider status to a campaign-visible outcome.

    Returns None for the non-terminal statuses ("queued", "sent", "sending"),
    which say the message is in flight and tell the campaign nothing new.
    """
    status = (provider_status or "").strip().lower()
    if status in DELIVERED_STATUSES:
        return "delivered"
    if status in UNDELIVERED_STATUSES:
        return "undelivered"
    return None


async def apply_sms_delivery_receipt(
    *,
    institution_id: str,
    workflow_run_id: str,
    message_sid: str,
    provider_status: str,
    provider_error: str | None = None,
) -> str | None:
    """Record a delivery receipt against the campaign step that sent the message.

    Opens its own institution-scoped session: the Twilio webhook runs under a
    system context with no institution, which is not a scope campaign tables
    should be read under. Mirrors how usage metering is recorded from the same
    handler.

    Returns the outcome recorded, or None when there was nothing to record.
    """
    outcome = classify_receipt(provider_status)
    if outcome is None:
        return None

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=message_sid,
    ) as session:
        step = (
            await session.execute(
                select(AutomationWorkflowStepExecution)
                .where(
                    AutomationWorkflowStepExecution.workflow_run_id == workflow_run_id,
                    AutomationWorkflowStepExecution.result_metadata[
                        "message_sid"
                    ].astext == message_sid,
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if step is None:
            # Not every SMS belongs to a campaign — agent-sent and inbound-reply
            # messages carry a run id but no send step of their own.
            logger.debug(
                "delivery receipt matched no campaign step: run=%s sid_present=%s",
                workflow_run_id, bool(message_sid),
            )
            return None

        metadata = dict(step.result_metadata or {})
        metadata["delivery_status"] = outcome
        metadata["delivery_reported_at"] = datetime.now(timezone.utc).isoformat()
        if outcome == "undelivered" and provider_error:
            metadata["delivery_error"] = provider_error
        step.result_metadata = metadata

        # The step still completed — we did send. What changed is whether it
        # arrived, which reporting must be able to tell apart.
        step.result_code = f"sent:{outcome}"
        await session.commit()

    logger.info(
        "delivery receipt applied: run=%s outcome=%s", workflow_run_id, outcome
    )
    return outcome
