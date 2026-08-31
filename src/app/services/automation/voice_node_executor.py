"""Executor for SendVoiceNode — places outbound campaign calls via Retell (Plan 03).

Places a per-location outbound AI call as a workflow action. The vendor HTTP call
is delegated to the mockable ``RetellOutboundClient`` (error classification lives
there). This executor is the orchestrator: idempotency guard, resolve
contact/location/creds, place the call, capture the ``retell_call_id`` onto the
attempt (so the post-call webhook can correlate the outcome back to this run), and
apply retry/give-up semantics.

Outcome handling: when ``node.wait_for_outcome`` is set, a successful placement
returns a ``VoiceParked`` signal so the dispatcher parks the run WAITING until the
Retell post-call webhook resumes it with the dial outcome. Otherwise the node is
fire-and-forget (advances immediately).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.definition_schema import SendVoiceNode
from src.app.services.automation.merge_field_catalog import MergeContextBuilder
from src.app.services.automation.retell_outbound_client import (
    RetellAmbiguousError,
    RetellOutboundClient,
    RetellPermanentError,
    RetellTransientError,
)
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.circuit_breaker import (
    BreakerService,
    NoOpCircuitBreaker,
    ServiceBreaker,
)
from src.app.services.automation.voice_attempt_recorder import (
    claim_voice_attempt,
    mark_attempt_failed,
    mark_attempt_placed,
    recent_voice_attempt_for_contact,
    resolve_outbound_voice_profile,
    voice_send_already_claimed,
)
from src.app.services.sms_privacy import mask_phone, normalize_phone

logger = logging.getLogger(__name__)

_CALL_PLACED = "call_placed"
# A placed call that is parked WAITING for its post-call outcome webhook. Distinct
# from a completed fire-and-forget "call_placed" so resume knows the call already
# went out (advance past, never re-dial).
_CALL_PLACED_AWAITING = "call_placed_awaiting_outcome"


def _voice_config_metadata(
    *,
    node: SendVoiceNode,
    profile: object | None,
    raw_from_number: str | None,
    from_number: str | None,
    raw_to_number: str | None,
    to_number: str | None,
) -> dict:
    profile_agent_id = getattr(profile, "retell_agent_id", None) if profile else None
    profile_from_number = getattr(profile, "retell_from_number", None) if profile else None
    return {
        "voice_profile_id": node.voice_profile_id,
        "voice_profile_name": getattr(profile, "display_name", None) if profile else None,
        "retell_agent_configured": bool((profile_agent_id or node.retell_agent_id or "").strip()),
        "retell_agent_source": "profile" if profile_agent_id else "node",
        "retell_from_number_source": "profile" if profile_from_number else "location",
        "retell_from_number_masked": mask_phone(from_number or raw_from_number),
        "to_number_masked": mask_phone(to_number or raw_to_number),
        "to_number_normalized": bool(raw_to_number and to_number and raw_to_number != to_number),
        "phone_country_code_enabled": node.phone_country_code_enabled,
        "phone_country_region": node.phone_country_region
        if node.phone_country_code_enabled
        else None,
        "retell_from_number_normalized": bool(
            raw_from_number and from_number and raw_from_number != from_number
        ),
    }


@dataclass(frozen=True)
class VoiceParked:
    """Signal to the dispatcher: the call was placed and the run should PARK
    WAITING for the outcome webhook. Carries the parked step + safety-timeout."""

    step: object  # AutomationWorkflowStepExecution (the WAITING step to wait_run + timer)
    timeout_minutes: int = 30


@dataclass(frozen=True)
class VoiceCooldownDeferred:
    """Signal that a voice send should retry at the patient cooldown boundary."""

    step: object
    due_at: datetime


def _context_deadline(value: object) -> datetime | None:
    """Parse an optional UTC workflow-context deadline."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ai_call_disclosure(clinic_name: str | None) -> str:
    """Spoken AI-call identity disclosure + opt-out (TCPA artificial-voice / CASL).
    Passed to Retell as a dynamic variable so the agent prompt opens every outbound
    call by identifying the clinic, stating the call is automated, and offering an
    opt-out. Spoken delivery lives in the Retell agent prompt (must reference
    ``{{compliance_disclosure}}``); this is the authoritative text the engine supplies."""
    clinic = (clinic_name or "your dental clinic").strip() or "your dental clinic"
    return (
        f"This is an automated call from {clinic}. "
        "If you would prefer not to receive automated calls, say 'stop' at any "
        "time and we will not call you again."
    )


class VoiceNodeExecutor:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AutomationWorkflowRuntimeService,
        breaker: ServiceBreaker | None = None,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.breaker: ServiceBreaker = breaker or NoOpCircuitBreaker()

    async def execute(
        self,
        run: AutomationWorkflowRun,
        node: SendVoiceNode,
        context: dict,
    ) -> str | VoiceParked | VoiceCooldownDeferred:
        """Place an outbound call. Returns next_node_id (fire-and-forget) or a
        VoiceParked signal (wait-for-outcome). On unrecoverable failure the step and
        run are failed; on a transient Retell error the exception is re-raised so the
        Celery task retries (until node.max_attempts is exhausted)."""
        # Send-time idempotency (XC-1): a redelivery / re-advance / hold-resume that
        # re-enters this node must not dial the patient again.
        if await self.runtime.already_sent(run, node.id):
            logger.info(
                "send_voice idempotent skip: call already placed institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

        # Crash-safe idempotency (P9): a committed non-FAILED voice-attempt claim means
        # a call was (or may have been, if we crashed between the Retell POST and the
        # commit) already placed for this (run, step). Bias to at-most-once — do NOT
        # re-dial. A FAILED claim doesn't count, so a V-6 transient retry still re-dials.
        if await voice_send_already_claimed(self.session, run.id, node.id):
            logger.info(
                "send_voice idempotent skip: committed claim exists institution=%s run=%s node=%s",
                run.institution_id, run.id, node.id,
            )
            return node.next_node_id

        step = await self.runtime.begin_step(run, step_id=node.id, step_type=node.type)

        # --- Resolve contact / phone / location / creds (all permanent failures) ---
        if not run.contact_id:
            await self.runtime.fail_step(step, result_code="no_contact")
            await self.runtime.fail_run(run, reason="send_voice: no contact_id on run")
            return node.next_node_id

        contact: Contact | None = await self.session.get(Contact, run.contact_id)
        if contact is None:
            await self.runtime.fail_step(step, result_code="contact_not_found")
            await self.runtime.fail_run(run, reason=f"send_voice: contact {run.contact_id} not found")
            return node.next_node_id

        raw_to_number = contact.phone
        if not raw_to_number:
            await self.runtime.fail_step(step, result_code="no_phone")
            await self.runtime.fail_run(run, reason="send_voice: contact has no phone number")
            return node.next_node_id
        default_phone_region = (
            node.phone_country_region
            if node.phone_country_code_enabled and node.phone_country_region
            else None
        )
        to_number = normalize_phone(raw_to_number, default_region=default_phone_region)
        if not to_number:
            await self.runtime.fail_step(
                step,
                result_code="invalid_phone",
                result_metadata={
                    "to_number_masked": mask_phone(raw_to_number),
                    "to_number_normalized": False,
                    "phone_country_code_enabled": node.phone_country_code_enabled,
                    "phone_country_region": node.phone_country_region
                    if node.phone_country_code_enabled
                    else None,
                },
            )
            await self.runtime.fail_run(run, reason="send_voice: contact phone number is invalid")
            return node.next_node_id

        cooldown_hours = node.patient_voice_cooldown_hours
        if cooldown_hours > 0:
            recent_attempt = await recent_voice_attempt_for_contact(
                self.session,
                institution_id=str(run.institution_id),
                contact_id=str(run.contact_id),
                since=datetime.now(timezone.utc) - timedelta(hours=cooldown_hours),
                excluding_workflow_run_id=str(run.id),
            )
            if recent_attempt is not None:
                recent_attempt_created_at = recent_attempt.created_at
                cooldown_expires_at = (
                    recent_attempt_created_at + timedelta(hours=cooldown_hours)
                    if recent_attempt_created_at is not None
                    else None
                )
                deadline = _context_deadline(
                    context.get(node.patient_voice_cooldown_deadline_field)
                    if node.patient_voice_cooldown_deadline_field
                    else None
                )
                if (
                    node.patient_voice_cooldown_behavior == "defer"
                    and cooldown_expires_at is not None
                    and (deadline is None or cooldown_expires_at <= deadline)
                ):
                    step.result_metadata = {
                        "patient_voice_cooldown_hours": cooldown_hours,
                        "recent_attempt_id": str(recent_attempt.id),
                        "recent_attempt_created_at": recent_attempt_created_at.isoformat(),
                        "cooldown_expires_at": cooldown_expires_at.isoformat(),
                        "cooldown_deadline_at": deadline.isoformat() if deadline else None,
                    }
                    logger.info(
                        "send_voice cooldown deferred: institution=%s run=%s node=%s until=%s",
                        run.institution_id,
                        run.id,
                        node.id,
                        cooldown_expires_at,
                    )
                    return VoiceCooldownDeferred(step=step, due_at=cooldown_expires_at)
                context["call_outcome"] = "voice_cooldown_skipped"
                if node.patient_voice_cooldown_behavior == "defer" and deadline is not None:
                    context["call_outcome"] = "voice_cooldown_window_expired"
                await self.runtime.complete_step(
                    step,
                    result_code="voice_cooldown_skipped",
                    result_metadata={
                        "patient_voice_cooldown_hours": cooldown_hours,
                        "recent_attempt_id": str(recent_attempt.id),
                        "recent_attempt_created_at": (
                            recent_attempt_created_at.isoformat()
                            if recent_attempt_created_at is not None
                            else None
                        ),
                        "to_number_masked": mask_phone(to_number),
                        "to_number_normalized": bool(
                            raw_to_number and to_number and raw_to_number != to_number
                        ),
                    },
                )
                logger.info(
                    "send_voice cooldown skip: institution=%s run=%s node=%s contact=%s cooldown_hours=%s recent_attempt=%s",
                    run.institution_id,
                    run.id,
                    node.id,
                    run.contact_id,
                    cooldown_hours,
                    recent_attempt.id,
                )
                return node.next_node_id

        location: InstitutionLocation | None = (
            await self.session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        # New workflows select a named outbound profile. Legacy workflows without
        # voice_profile_id retain the older profile/node/location fallback behavior.
        profile = await resolve_outbound_voice_profile(
            self.session,
            run.location_id,
            node.voice_profile_id,
        )
        if node.voice_profile_id and profile is None:
            await self.runtime.fail_step(
                step,
                result_code="voice_profile_not_found",
                result_metadata={"voice_profile_id": node.voice_profile_id},
            )
            await self.runtime.fail_run(
                run,
                reason="send_voice: selected outbound voice profile is missing or inactive",
            )
            return node.next_node_id
        raw_from_number = (
            (profile.retell_from_number if profile and profile.retell_from_number else None)
            or (location.retell_from_number if location else None)
        )
        if not raw_from_number:
            await self.runtime.fail_step(step, result_code="no_from_number")
            await self.runtime.fail_run(run, reason="send_voice: location has no retell_from_number")
            return node.next_node_id
        from_number = normalize_phone(raw_from_number)
        if not from_number:
            await self.runtime.fail_step(
                step,
                result_code="invalid_from_number",
                result_metadata={
                    "voice_profile_id": node.voice_profile_id,
                    "voice_profile_name": getattr(profile, "display_name", None) if profile else None,
                    "retell_from_number_masked": mask_phone(raw_from_number),
                    "retell_from_number_normalized": False,
                    "to_number_masked": mask_phone(to_number),
                    "to_number_normalized": raw_to_number != to_number,
                },
            )
            await self.runtime.fail_run(run, reason="send_voice: retell_from_number is invalid")
            return node.next_node_id
        agent_id = (
            profile.retell_agent_id if profile and profile.retell_agent_id else node.retell_agent_id
        )
        voice_metadata = _voice_config_metadata(
            node=node,
            profile=profile,
            raw_from_number=raw_from_number,
            from_number=from_number,
            raw_to_number=raw_to_number,
            to_number=to_number,
        )
        if not (agent_id or "").strip():
            await self.runtime.fail_step(
                step,
                result_code="no_retell_agent",
                result_metadata=voice_metadata,
            )
            await self.runtime.fail_run(
                run,
                reason="send_voice: no Retell agent selected for outbound voice",
            )
            return node.next_node_id

        api_key = settings.retell_api_secret
        if not api_key:
            await self.runtime.fail_step(
                step,
                result_code="retell_not_configured",
                result_metadata=voice_metadata,
            )
            await self.runtime.fail_run(run, reason="send_voice: Retell not configured (RETELL_API_SECRET)")
            return node.next_node_id

        clinic_name = getattr(location, "name", None)
        merge_context = MergeContextBuilder.build(
            contact=contact,
            location=location,
            context=MergeContextBuilder.normalize_raw_context(context),
        )
        patient_first_name = merge_context.get("patient_first_name", "")
        dynamic_variables = {
            **merge_context,
            "patient_first_name": patient_first_name,
            "first_name": patient_first_name or merge_context.get("first_name", ""),
            "user_number": to_number,
            "clinic_name": clinic_name or "",
            "compliance_disclosure": _ai_call_disclosure(clinic_name),
        }
        metadata = {
            "workflow_run_id": str(run.id),
            # workflow_id lets the Retell post-call usage webhook attribute voice
            # minutes/dials to the campaign in /by-campaign (Plan 11), symmetric
            # with SMS/email. Without it voice is invisible in per-campaign spend.
            "workflow_id": str(run.workflow_id),
            "workflow_step_id": node.id,
            "institution_id": str(run.institution_id),
            "source": "outbound_campaign",
            "ai_automated_call": True,
        }

        # --- Crash-safe claim (P9): commit an INITIATING attempt BEFORE the POST so a
        # crash between the Retell POST and the task commit leaves a durable claim that
        # blocks a re-dial. Retell has no idempotency key (A-4), so this is the mechanism. ---
        attempt = await claim_voice_attempt(
            self.session, run, step, from_number=from_number, to_number=to_number
        )
        await self.session.commit()

        # --- Place the call via the mockable client ---
        try:
            result = await RetellOutboundClient(api_key).create_phone_call(
                from_number=from_number,
                to_number=to_number,
                override_agent_id=agent_id,
                dynamic_variables=dynamic_variables,
                metadata=metadata,
            )
        except RetellTransientError as exc:
            await self.breaker.record_failure(
                BreakerService.RETELL, str(run.location_id or run.institution_id)
            )
            # Recoverable vendor blip. The POST did not succeed → mark the claim FAILED
            # (committed) so the V-6 retry sees no active claim and can re-dial. Then
            # retry via the Celery task until max_attempts, then give up.
            await mark_attempt_failed(attempt, error_message=str(exc))
            await self.session.commit()
            await self.runtime.fail_step(
                step,
                result_code="retrying_transient",
                error_message=str(exc),
                result_metadata=voice_metadata,
            )
            if step.attempt_number >= node.max_attempts:
                logger.error(
                    "send_voice transient error, attempts exhausted (%d): run=%s node=%s err=%s",
                    node.max_attempts, run.id, node.id, exc,
                )
                await self.runtime.fail_run(run, reason="send_voice: transient error, attempts exhausted")
                return node.next_node_id
            logger.warning(
                "send_voice transient error (attempt %d/%d), re-raising for retry: run=%s node=%s err=%s",
                step.attempt_number, node.max_attempts, run.id, node.id, exc,
            )
            raise  # propagate → Celery task retries with backoff
        except RetellAmbiguousError as exc:
            # Counted against the breaker even though this call is never retried:
            # a timeout is the clearest signal Retell is unwell, and the breaker
            # is what stops the *next* run walking into the same wall.
            await self.breaker.record_failure(
                BreakerService.RETELL, str(run.location_id or run.institution_id)
            )
            # Timeout/network (XC-1b): the call MAY have been placed but the response
            # was lost. Do NOT retry (no idempotency key → double-dial risk). Fail the
            # run, but leave the claim INITIATING (NOT failed) so a task redelivery is
            # still blocked by voice_send_already_claimed (at-most-once). Record why.
            attempt.error_message = str(exc)
            await self.session.commit()  # persist the ambiguous, still-blocking claim
            logger.warning(
                "send_voice ambiguous timeout, not retrying (call may have been placed): "
                "institution=%s run=%s node=%s err=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await self.runtime.fail_step(
                step,
                result_code="send_ambiguous_no_retry",
                error_message=str(exc),
                result_metadata=voice_metadata,
            )
            await self.runtime.fail_run(
                run, reason="send_voice: ambiguous timeout, not retrying (at-most-once)"
            )
            return node.next_node_id
        except (RetellPermanentError, Exception) as exc:  # noqa: BLE001
            logger.error(
                "send_voice permanent failure: institution=%s run=%s node=%s error=%s",
                run.institution_id, run.id, node.id, exc,
            )
            await mark_attempt_failed(attempt, error_message=str(exc))
            await self.session.commit()
            await self.runtime.fail_step(
                step,
                result_code="send_failed",
                error_message=str(exc),
                result_metadata=voice_metadata,
            )
            await self.runtime.fail_run(run, reason=f"send_voice error: {type(exc).__name__}")
            return node.next_node_id

        # --- Placed successfully: transition the committed claim to placed/awaiting
        # (V-4) + store the retell_call_id on the step for webhook correlation. A crash
        # before the task commit leaves the claim INITIATING, which still blocks a
        # re-dial on redelivery (at-most-once). ---
        await self.breaker.record_success(
            BreakerService.RETELL, str(run.location_id or run.institution_id)
        )
        await mark_attempt_placed(
            attempt, retell_call_id=result.call_id, awaiting_outcome=node.wait_for_outcome
        )

        if node.wait_for_outcome:
            # Park WAITING for the outcome webhook. Keep the step WAITING with the
            # placed-call marker + retell_call_id so resume advances past (never re-dials).
            await self.runtime.mark_step_awaiting_outcome(
                step,
                result_code=_CALL_PLACED_AWAITING,
                result_metadata={**voice_metadata, "retell_call_id": result.call_id},
            )
            return VoiceParked(step=step)

        await self.runtime.complete_step(
            step,
            result_code=_CALL_PLACED,
            result_metadata={**voice_metadata, "retell_call_id": result.call_id},
        )
        return node.next_node_id
