"""
Audit logging decorator with explicit configuration.

Design Principles:
- Explicit is better than Implicit: No magic string guessing.
- Fail Safe: Configuration errors are critical and logged immediately.
- Flexible: Supports static strings or callable extractors for resource IDs.

Usage:
    @audit(AuditAction.READ_PATIENT, resource=lambda args: f"patient:{args['id']}")
    async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
        ...
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar, Union

from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.security import get_client_ip
from src.app.services.audit import (
    AuditPersistenceError,
    log_audit,
    log_audit_background,
)

# Mutating / explicit PHI-reveal actions for which audit MUST be durable
# (await before returning). High-volume clinic reads such as debounced patient
# search stay best-effort/background and are drained on shutdown; explicit
# full patient/detail reveals use phi_reveal_audit or VIEW_* and fail closed.
DURABLE_AUDIT_ACTIONS: frozenset[str] = frozenset({
    AuditAction.BOOK_APPOINTMENT.value,
    AuditAction.CANCEL_APPOINTMENT.value,
    AuditAction.RESCHEDULE_APPOINTMENT.value,
    AuditAction.CREATE_PATIENT.value,
    AuditAction.UPDATE_PATIENT.value,
    AuditAction.VIEW_FULL_TRANSCRIPT.value,
    AuditAction.VIEW_CALL_RECORDING.value,
    AuditAction.VIEW_FULL_PHONE.value,
    AuditAction.VIEW_SMS_BODY.value,
    AuditAction.VIEW_CUSTOM_PHI_FIELD.value,
})

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
ResourceExtractor = Union[str, Callable[..., str]]


def audit(
    action: AuditAction | str,
    resource: ResourceExtractor,
    actor: AuditActor | str = AuditActor.RETELL_AGENT,
) -> Callable[[F], F]:
    """
    Decorator for automatic audit logging with EXPLICIT resource extraction.

    Args:
        action: The audit action being performed.
        resource: Either a static string (rare) or a callable that extracts the
                  resource identifier from the decorated function's arguments.
                  The callable receives the same *args and **kwargs as the function.
        actor: Who is performing the action.
    
    Example:
        @audit(AuditAction.READ_PATIENT, resource=lambda args: f"patient:{args.get('patient_id')}")
    """
    
    action_value = action.value if isinstance(action, AuditAction) else str(action)
    is_durable = action_value in DURABLE_AUDIT_ACTIONS

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from uuid import uuid4
            request_id = str(uuid4())

            # 1. Extract resource identifier from the function's args. Failure
            #    here is a developer bug — we log loudly but continue.
            target_resource = "unknown"
            config_error: str | None = None
            try:
                if callable(resource):
                    target_resource = resource(*args, **kwargs)
                else:
                    target_resource = str(resource)
                if not target_resource:
                    raise ValueError("Extractor returned empty string")
            except Exception as e:
                config_error = f"Audit extraction failed: {e}"
                logger.critical("AUDIT CONFIG ERROR in %s: %s", func.__name__, config_error)
                target_resource = f"{action_value}:CONFIGURATION_ERROR"

            # 2. Pre-call context (request, user, IP). Institution may not be
            #    resolvable yet for Retell handlers — re-checked after the call.
            actor_ctx = _resolve_actor_context(args, kwargs)
            client_ip = _resolve_client_ip(args, kwargs)

            base_metadata: dict[str, Any] = {
                "request_id": request_id,
                "function_name": func.__name__,
            }
            if actor_ctx.get("actor_user_id"):
                base_metadata["actor_user_id"] = actor_ctx["actor_user_id"]
            if actor_ctx.get("actor_role"):
                base_metadata["actor_role"] = actor_ctx["actor_role"]
            if client_ip:
                base_metadata["ip_address"] = client_ip
            if config_error:
                base_metadata["config_error"] = config_error

            # 3. Durable actions (PMS bookings, PHI reveals) use a two-row
            #    pre-then-post pattern so a side-effect can never happen
            #    without an audit breadcrumb:
            #
            #       INITIATED row (pre)  →  func() runs  →  outcome row (post)
            #
            #    If the INITIATED write fails we refuse to call func() at
            #    all — no PMS booking, no PHI reveal. If the post-action
            #    write fails after func() succeeded, the INITIATED row is
            #    still there with a shared request_id and operators can
            #    reconcile from a "INITIATED with no completion" report.
            #    Reports filtering on completed work should exclude
            #    outcome=INITIATED.
            if is_durable:
                try:
                    await log_audit(
                        actor=actor,
                        action=action,
                        target_resource=target_resource,
                        outcome=AuditOutcome.INITIATED,
                        metadata={**base_metadata, "phase": "intent"},
                        institution_id=actor_ctx.get("institution_id"),
                        user_id=actor_ctx.get("actor_user_id"),
                        location_id=actor_ctx.get("location_id"),
                        request_id=request_id,
                    )
                except AuditPersistenceError:
                    # No audit, no side-effect — fail closed before func().
                    raise

            # 4. Run the wrapped function and capture outcome.
            #    Two failure modes:
            #     a) The function raises — _classify_exception() picks the
            #        outcome from the exception type / status code.
            #     b) The function returns a soft-error dict (Retell handlers
            #        always return a dict so the voice agent can speak the
            #        message — they signal failure with "success": False or
            #        "error": "..."). Without this branch, a failed booking
            #        or rejected validation lands in audit_logs as SUCCESS,
            #        which is a HIPAA-relevant audit-trail integrity bug.
            outcome: AuditOutcome | str
            error: Exception | None = None
            result: Any = None
            try:
                result = await func(*args, **kwargs)
                soft_outcome, soft_error_message = _classify_soft_error(result)
                if soft_outcome is not None:
                    outcome = soft_outcome
                    if soft_error_message:
                        base_metadata["error_message"] = soft_error_message
                    base_metadata["error_kind"] = "soft_failure"
                else:
                    outcome = AuditOutcome.SUCCESS
                _augment_metadata_for_action(
                    base_metadata,
                    action_value=action_value,
                    result=result,
                    args=args,
                    kwargs=kwargs,
                )
            except Exception as e:
                error = e
                outcome = _classify_exception(e)
                base_metadata["error_type"] = type(e).__name__
                _augment_metadata_for_action(
                    base_metadata,
                    action_value=action_value,
                    result=None,
                    args=args,
                    kwargs=kwargs,
                )

            # 5. Resolve institution AFTER the call — Retell handlers stash the
            #    institution onto the call context inside _resolve_context(), so
            #    we read it here, not before.
            institution_id = _resolve_institution_id(
                args, kwargs, actor_ctx.get("institution_id")
            )
            location_id = (
                actor_ctx.get("location_id")
                or _resolve_location_id_from_retell_context()
            )
            if location_id and "location_id" not in base_metadata:
                base_metadata["location_id"] = location_id

            # 6. Persist the post-action row. Durable: synchronous; if it
            #    fails the caller gets an error AND the INITIATED breadcrumb
            #    from step 3 lets operators reconcile via request_id. Reads
            #    stay best-effort (background).
            post_metadata = (
                {**base_metadata, "phase": "complete"}
                if is_durable
                else base_metadata
            )
            audit_persistence_error: AuditPersistenceError | None = None
            if is_durable:
                try:
                    await log_audit(
                        actor=actor,
                        action=action,
                        target_resource=target_resource,
                        outcome=outcome,
                        metadata=post_metadata,
                        institution_id=institution_id,
                        user_id=actor_ctx.get("actor_user_id"),
                        location_id=location_id,
                        request_id=request_id,
                    )
                except AuditPersistenceError as audit_err:
                    # Already logged at CRITICAL inside the service; capture
                    # and decide below whether to raise.
                    audit_persistence_error = audit_err
            else:
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata=post_metadata,
                    institution_id=institution_id,
                    user_id=actor_ctx.get("actor_user_id"),
                    location_id=location_id,
                    request_id=request_id,
                )

            # Function-level exception takes precedence — it's the most
            # informative signal for the caller.
            if error is not None:
                raise error
            # Function succeeded but post-action audit write failed → fail
            # loud. The downstream side-effect has already happened; the
            # INITIATED row from step 3 plus the CRITICAL log line are the
            # reconciliation handles.
            if audit_persistence_error is not None:
                raise audit_persistence_error
            return result

        return wrapper  # type: ignore

    return decorator


def _resolve_institution_id(args: tuple, kwargs: dict, fallback: str | None = None) -> str | None:
    """Resolve institution ID from request.state, current_user, or Retell call context."""
    for arg in args:
        if hasattr(arg, "state") and hasattr(arg.state, "institution"):
            institution = getattr(arg.state, "institution", None)
            if institution:
                return str(institution.id)

    # Retell handlers receive only an args dict — no Request, no User.
    # The handler stashes resolved IDs onto a ContextVar after _resolve_context().
    try:
        from src.app.retell.functions import get_call_context
        call_ctx = get_call_context()
        if call_ctx.get("institution_id"):
            return str(call_ctx["institution_id"])
    except Exception:
        pass

    return fallback


def _augment_metadata_for_action(
    metadata: dict[str, Any],
    *,
    action_value: str,
    result: Any,
    args: tuple,
    kwargs: dict,
) -> None:
    """Add non-PHI audit context that helps compliance review."""
    if action_value != AuditAction.SEARCH_PATIENTS.value:
        return

    metadata["high_volume_read"] = True
    criteria = _patient_search_criteria(args, kwargs)
    if criteria:
        metadata["search_criteria"] = criteria

    result_count = _result_count(result)
    if result_count is not None:
        metadata["result_count"] = result_count


def _patient_search_criteria(args: tuple, kwargs: dict) -> list[str]:
    source: dict[str, Any] = {}
    if args and isinstance(args[0], dict):
        source.update(args[0])
    source.update(kwargs)

    criteria: list[str] = []
    for key, label in (
        ("q", "name"),
        ("name", "name"),
        ("email", "email"),
        ("phone_number", "phone"),
        ("date_of_birth", "dob"),
    ):
        if source.get(key) and label not in criteria:
            criteria.append(label)
    return criteria


def _result_count(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        count = result.get("count")
        if isinstance(count, int):
            return count
        patients = result.get("patients")
        if isinstance(patients, list):
            return len(patients)
    return None


def _resolve_location_id_from_retell_context() -> str | None:
    """Read the location ID stashed by the Retell handler, if any."""
    try:
        from src.app.retell.functions import get_call_context
        loc_id = get_call_context().get("location_id")
        return str(loc_id) if loc_id else None
    except Exception:
        return None


def _resolve_actor_context(args: tuple, kwargs: dict) -> dict[str, str | None]:
    """
    Resolve actor user metadata when a route function receives `current_user` dependency.
    """
    candidate_values = list(args) + list(kwargs.values())
    for value in candidate_values:
        # User model shape: id, role, institution_id, location_id.
        if all(hasattr(value, attr) for attr in ("id", "role", "institution_id", "location_id")):
            return {
                "actor_user_id": str(getattr(value, "id", None)) if getattr(value, "id", None) else None,
                "actor_role": str(getattr(value, "role", None)) if getattr(value, "role", None) else None,
                "institution_id": str(getattr(value, "institution_id", None)) if getattr(value, "institution_id", None) else None,
                "location_id": str(getattr(value, "location_id", None)) if getattr(value, "location_id", None) else None,
            }
    return {
        "actor_user_id": None,
        "actor_role": None,
        "institution_id": None,
        "location_id": None,
    }


def _resolve_client_ip(args: tuple, kwargs: dict) -> str | None:
    """Extract client IP from a Starlette Request in the call arguments.

    Uses ``isinstance`` rather than duck-typing so AsyncMock and other
    duck-typed test doubles don't accidentally match.
    """
    from starlette.requests import Request

    for arg in list(args) + list(kwargs.values()):
        if isinstance(arg, Request):
            direct_host = arg.client.host if arg.client else None
            return get_client_ip(
                forwarded_for=arg.headers.get("x-forwarded-for"),
                direct_host=direct_host,
            )
    return None


def _classify_exception(e: Exception) -> AuditOutcome:
    """Map exception to AuditOutcome."""
    from fastapi import HTTPException

    if isinstance(e, HTTPException):
        if e.status_code in (401, 403):
            return AuditOutcome.FAILURE_UNAUTHORIZED
        if e.status_code == 404:
            return AuditOutcome.FAILURE_NOT_FOUND
        if e.status_code in (400, 422):
            return AuditOutcome.FAILURE_VALIDATION

    return AuditOutcome.FAILURE_INTERNAL


def _classify_soft_error(result: Any) -> tuple[AuditOutcome | None, str | None]:
    """Detect soft-failure signals in a handler's return value.

    Retell function handlers always return a dict so the voice agent can read
    the response back to the caller. A failed booking or validation error is
    signalled with one of:
      - ``{"success": False, ...}``
      - ``{"error": "<message>", ...}``

    When neither sentinel is present the result is treated as a success.
    Returns ``(outcome, error_message)`` where ``outcome`` is ``None`` for
    successes.
    """
    if not isinstance(result, dict):
        return None, None

    error_message = result.get("error")
    if isinstance(error_message, str) and error_message.strip():
        return AuditOutcome.FAILURE_VALIDATION, error_message[:200]

    if result.get("success") is False:
        # No "error" key but explicit success=False — fall back to "message"
        # for forensics.
        message = result.get("message")
        msg = str(message)[:200] if message else None
        return AuditOutcome.FAILURE_VALIDATION, msg

    return None, None
