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
from src.app.services.audit import get_audit_service, log_audit_background

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
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from uuid import uuid4
            request_id = str(uuid4())
            
            # 1. Extract Resource ID (Explicitly)
            target_resource = "unknown"
            config_error = None
            
            try:
                if callable(resource):
                    # Pass all args to the extractor. 
                    # For Retell handlers: func(args_dict) -> extractor(args_dict)
                    # For API routes: func(req, id, ...) -> extractor(req, id, ...)
                    target_resource = resource(*args, **kwargs)
                else:
                    target_resource = str(resource)
                    
                if not target_resource:
                    raise ValueError("Extractor returned empty string")
                    
            except Exception as e:
                # CRITICAL: The developer configured this audit incorrectly.
                # We catch this to prevent crashing the app, but we log LOUDLY.
                config_error = f"Audit Extraction Failed: {str(e)}"
                logger.critical(f"AUDIT CONFIG ERROR in {func.__name__}: {config_error}")
                target_resource = f"{action}:CONFIGURATION_ERROR"

            # 2. Context Resolution (Institution, IP, etc.)
            actor_ctx = _resolve_actor_context(args, kwargs)
            institution_id = _resolve_institution_id(args, kwargs, actor_ctx.get("institution_id"))
            client_ip = _resolve_client_ip(args, kwargs)

            # Base metadata
            safe_metadata: dict[str, Any] = {
                "request_id": request_id,
                "function_name": func.__name__,
            }
            if actor_ctx.get("actor_user_id"):
                safe_metadata["actor_user_id"] = actor_ctx["actor_user_id"]
            if actor_ctx.get("actor_role"):
                safe_metadata["actor_role"] = actor_ctx["actor_role"]
            if actor_ctx.get("location_id"):
                safe_metadata["location_id"] = actor_ctx["location_id"]
            if client_ip:
                safe_metadata["ip_address"] = client_ip
            if config_error:
                safe_metadata["config_error"] = config_error

            # 3. Execute & Log Outcome
            try:
                result = await func(*args, **kwargs)
                
                # Success
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=AuditOutcome.SUCCESS,
                    metadata=safe_metadata,
                    institution_id=institution_id,
                    request_id=request_id,
                )
                return result
                
            except Exception as e:
                # Failure
                outcome = _classify_exception(e)
                safe_metadata["error_type"] = type(e).__name__
                
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata=safe_metadata,
                    institution_id=institution_id,
                    request_id=request_id,
                )
                raise

        return wrapper  # type: ignore
    
    return decorator


def _resolve_institution_id(args: tuple, kwargs: dict, fallback: str | None = None) -> str | None:
    """Attempt to resolve institution ID from arguments (Context)."""
    for arg in args:
        if hasattr(arg, "state") and hasattr(arg.state, "institution"):
            institution = getattr(arg.state, "institution", None)
            if institution:
                return str(institution.id)
    return fallback


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
    """
    Extract client IP from a FastAPI Request object in the call arguments.

    Prefers X-Forwarded-For (set by reverse proxies / Render's load balancer)
    and falls back to the direct connection IP.
    """
    for arg in args:
        if hasattr(arg, "headers") and hasattr(arg, "client"):
            # X-Forwarded-For may contain a comma-separated chain; take the first (original client)
            forwarded_for = arg.headers.get("x-forwarded-for")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
            if arg.client:
                return arg.client.host
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
