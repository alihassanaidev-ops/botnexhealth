"""Persistence helpers for the outbound-voice data model (Plan 03 / V-4).

Small, shared seam over ``outbound_voice_profiles`` and ``workflow_voice_attempts``
so the voice executor and the post-call resume task write them consistently, and so
the future crash-safe committed claim (P9) has one place to grow an ``initiating``
pre-POST insert. No raw phone numbers are stored — only masked forms (PHI-safe).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun, AutomationWorkflowStepExecution
from src.app.models.outbound_voice import (
    OutboundVoiceProfile,
    VoiceAttemptStatus,
    WorkflowVoiceAttempt,
)
from src.app.services.sms_privacy import mask_phone

logger = logging.getLogger(__name__)


async def resolve_outbound_voice_profile(
    session: AsyncSession, location_id: str | None
) -> OutboundVoiceProfile | None:
    """Return the active outbound-voice profile for a location, or None.

    Resolution is override-with-fallback: callers prefer the profile's
    agent/from-number when present and fall back to the node/location defaults, so
    an absent or inactive profile leaves existing behavior unchanged.
    """
    if not location_id:
        return None
    return (
        await session.execute(
            select(OutboundVoiceProfile).where(
                OutboundVoiceProfile.location_id == location_id,
                OutboundVoiceProfile.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


async def voice_send_already_claimed(
    session: AsyncSession, run_id: str, step_id: str
) -> bool:
    """True if a non-FAILED voice attempt is already committed for this
    ``(run, step)`` — i.e. a call was (or may have been) placed (P9 crash-safe
    idempotency).

    Distinct from ``runtime.already_sent`` (which keys on a COMPLETED step): this
    catches the crash-between-POST-and-commit tail where the committed claim is
    still ``INITIATING`` and the step never completed. A ``FAILED`` claim does NOT
    count, so a transient-error retry (V-6) can create a fresh claim and re-dial.
    """
    claimed = (
        await session.execute(
            select(WorkflowVoiceAttempt.id)
            .where(
                WorkflowVoiceAttempt.workflow_run_id == run_id,
                WorkflowVoiceAttempt.step_id == step_id,
                WorkflowVoiceAttempt.status != VoiceAttemptStatus.FAILED.value,
            )
            .limit(1)
        )
    ).scalar()
    return claimed is not None


async def claim_voice_attempt(
    session: AsyncSession,
    run: AutomationWorkflowRun,
    step: AutomationWorkflowStepExecution,
    *,
    from_number: str | None,
    to_number: str | None,
) -> WorkflowVoiceAttempt:
    """Insert an ``INITIATING`` claim row (no ``retell_call_id`` yet). The caller
    COMMITS it before the Retell POST so a crash between POST and commit leaves a
    durable claim that blocks a re-dial (P9). No raw phone numbers — masked only."""
    attempt = WorkflowVoiceAttempt(
        institution_id=run.institution_id,
        location_id=run.location_id,
        workflow_run_id=run.id,
        step_execution_id=step.id,
        step_id=step.step_id,
        attempt_number=step.attempt_number,
        retell_call_id=None,
        from_number_masked=mask_phone(from_number),
        to_number_masked=mask_phone(to_number),
        status=VoiceAttemptStatus.INITIATING.value,
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def mark_attempt_placed(
    attempt: WorkflowVoiceAttempt,
    *,
    retell_call_id: str | None,
    awaiting_outcome: bool,
) -> WorkflowVoiceAttempt:
    """Transition a claimed attempt to ``PLACED``/``AWAITING_OUTCOME`` after a
    successful POST, capturing the ``retell_call_id`` correlation key."""
    attempt.retell_call_id = retell_call_id
    attempt.status = (
        VoiceAttemptStatus.AWAITING_OUTCOME.value
        if awaiting_outcome
        else VoiceAttemptStatus.PLACED.value
    )
    return attempt


async def mark_attempt_failed(
    attempt: WorkflowVoiceAttempt, *, error_message: str | None = None
) -> WorkflowVoiceAttempt:
    """Transition a claimed attempt to ``FAILED`` (the POST did not succeed). The
    caller COMMITS so a subsequent V-6 retry sees no active claim and can re-dial."""
    attempt.status = VoiceAttemptStatus.FAILED.value
    if error_message is not None:
        attempt.error_message = error_message
    return attempt


async def record_placed_attempt(
    session: AsyncSession,
    run: AutomationWorkflowRun,
    step: AutomationWorkflowStepExecution,
    *,
    retell_call_id: str | None,
    from_number: str | None,
    to_number: str | None,
    awaiting_outcome: bool,
) -> WorkflowVoiceAttempt:
    """Insert an already-placed voice-attempt row in one shot (claim + place).

    Convenience for callers that do not need the crash-safe committed-before-POST
    claim (e.g. tests / non-idempotency-critical paths). The executor uses the
    two-phase ``claim_voice_attempt`` → ``mark_attempt_placed`` instead (P9).
    """
    attempt = await claim_voice_attempt(
        session, run, step, from_number=from_number, to_number=to_number
    )
    await mark_attempt_placed(
        attempt, retell_call_id=retell_call_id, awaiting_outcome=awaiting_outcome
    )
    await session.flush()
    return attempt


async def stamp_attempt_outcome(
    session: AsyncSession,
    *,
    institution_id: str,
    retell_call_id: str,
    dial_outcome: str,
    disconnection_reason: str | None = None,
) -> bool:
    """Resolve the awaiting attempt row (correlated by ``retell_call_id``) to
    COMPLETED with its normalized dial outcome. Returns False if no row matches
    (fire-and-forget / non-campaign calls have no awaiting row) — never raises."""
    attempt = (
        await session.execute(
            select(WorkflowVoiceAttempt).where(
                WorkflowVoiceAttempt.institution_id == institution_id,
                WorkflowVoiceAttempt.retell_call_id == retell_call_id,
            )
        )
    ).scalar_one_or_none()
    if attempt is None:
        return False
    attempt.status = VoiceAttemptStatus.COMPLETED.value
    attempt.dial_outcome = dial_outcome
    if disconnection_reason is not None:
        attempt.disconnection_reason = disconnection_reason
    await session.flush()
    return True
