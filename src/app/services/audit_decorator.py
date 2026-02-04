"""
Audit logging decorator for HIPAA compliance.

SOLID Principles Applied:
- OCP: Add audit logging to handlers without modifying them
- SRP: Decorator only handles audit concerns
- DIP: Uses the audit service abstraction

Usage:
    @audited(AuditAction.READ_PATIENT, resource_key="patient_id")
    async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
        ...

The decorator:
1. Logs the action before execution (with PENDING outcome)
2. Captures success/failure automatically
3. Extracts resource identifier from args or result
4. Associates with tenant if available
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def audited(
    action: AuditAction | str,
    actor: AuditActor | str = AuditActor.RETELL_AGENT,
    resource_key: str | None = None,
    resource_from_result: str | None = None,
) -> Callable[[F], F]:
    """
    Decorator for automatic audit logging on Retell function handlers.
    
    OCP: Extends handler behavior without modifying the handler code.
    
    Args:
        action: The audit action being performed
        actor: Who is performing the action (defaults to RETELL_AGENT for handlers)
        resource_key: Key in args dict to use as target_resource
        resource_from_result: Key in result dict to extract resource (e.g., "patient_id")
    
    Example:
        @audited(AuditAction.READ_PATIENT, resource_key="patient_id")
        async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
            ...
    
    The decorator will:
    - Extract target_resource from args[resource_key] or result[resource_from_result]
    - Log SUCCESS if function returns without exception
    - Log appropriate FAILURE_* if exception raised
    - Include metadata with args summary (no PHI)
    """
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
            # Import here to avoid circular imports
            from src.app.services.audit import get_audit_service, log_audit_background
            from src.app.retell.functions import get_tenant_from_call_context
            from uuid import uuid4
            
            request_id = str(uuid4())
            
            # Extract resource identifier from args
            target_resource = "unknown"
            if resource_key and resource_key in args:
                target_resource = f"{action}:{args[resource_key]}"
            elif resource_key:
                # Try common patterns
                target_resource = f"{action}:{args.get('patient_id') or args.get('appointment_id') or args.get('location_id') or 'unknown'}"
            
            # Get tenant context if available
            tenant_id = None
            try:
                tenant = await get_tenant_from_call_context()
                if tenant:
                    tenant_id = tenant.id
            except Exception:
                pass  # No tenant context available
            
            # Prepare safe metadata (no PHI!)
            safe_metadata = {
                "request_id": request_id,
                "function_name": func.__name__,
                "has_location_id": bool(args.get("location_id")),
                "has_subdomain": bool(args.get("subdomain")),
                "pms_provider": args.get("pms_provider", "nexhealth"),
            }
            
            try:
                # Execute the actual function
                result = await func(args)
                
                # Update target_resource from result if configured
                if resource_from_result and isinstance(result, dict):
                    resource_value = result.get(resource_from_result)
                    if resource_value:
                        target_resource = f"{action}:{resource_value}"
                
                # Determine outcome based on result
                outcome = AuditOutcome.SUCCESS
                if isinstance(result, dict):
                    if result.get("error"):
                        outcome = AuditOutcome.FAILURE_VALIDATION
                    elif result.get("success") is False:
                        outcome = AuditOutcome.FAILURE_EXTERNAL_API
                
                # Log audit in background (non-blocking)
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata=safe_metadata,
                    tenant_id=tenant_id,
                    request_id=request_id,
                )
                
                return result
                
            except Exception as e:
                # Log failure
                from fastapi import HTTPException
                
                if isinstance(e, HTTPException):
                    if e.status_code == 401 or e.status_code == 403:
                        outcome = AuditOutcome.FAILURE_UNAUTHORIZED
                    elif e.status_code == 404:
                        outcome = AuditOutcome.FAILURE_NOT_FOUND
                    else:
                        outcome = AuditOutcome.FAILURE_VALIDATION
                else:
                    outcome = AuditOutcome.FAILURE_INTERNAL
                
                safe_metadata["error_type"] = type(e).__name__
                
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata=safe_metadata,
                    tenant_id=tenant_id,
                    request_id=request_id,
                )
                
                raise
        
        return wrapper  # type: ignore
    
    return decorator


def audited_api(
    action: AuditAction | str,
    actor: AuditActor | str = AuditActor.API_CLIENT,
    resource_extractor: Callable[..., str] | None = None,
    resource_key: str | None = None,
) -> Callable[[F], F]:
    """
    Decorator for FastAPI route audit logging.
    
    Similar to @audited but designed for API routes with Request context.
    
    Args:
        action: The audit action being performed
        actor: Who is performing the action
        resource_extractor: Optional function to extract resource from route args
        resource_key: Simple key to extract from kwargs as resource (e.g., "id", "slug")
    
    Example:
        @router.get("/patients/{patient_id}")
        @audited_api(AuditAction.READ_PATIENT, resource_key="patient_id")
        async def get_patient(request: Request, patient_id: int, ...):
            ...
    """
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from src.app.services.audit import log_audit_background
            from uuid import uuid4
            
            request_id = str(uuid4())
            
            # Extract resource
            target_resource = "unknown"
            if resource_extractor:
                try:
                    target_resource = resource_extractor(**kwargs)
                except Exception:
                    pass
            elif resource_key and resource_key in kwargs:
                target_resource = f"{action}:{kwargs[resource_key]}"
            
            # Try to get IP from request if available
            ip_address = None
            tenant_id = None
            for arg in args:
                if hasattr(arg, "client") and hasattr(arg.client, "host"):
                    ip_address = arg.client.host
                if hasattr(arg, "state") and hasattr(arg.state, "tenant"):
                    tenant = getattr(arg.state, "tenant", None)
                    if tenant:
                        tenant_id = tenant.id
                    break
            
            safe_metadata = {
                "request_id": request_id,
                "function_name": func.__name__,
            }
            if ip_address:
                safe_metadata["ip_address"] = ip_address
            
            try:
                result = await func(*args, **kwargs)
                
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=AuditOutcome.SUCCESS,
                    metadata=safe_metadata,
                    tenant_id=tenant_id,
                    request_id=request_id,
                )
                
                return result
                
            except Exception as e:
                from fastapi import HTTPException
                
                if isinstance(e, HTTPException):
                    if e.status_code in (401, 403):
                        outcome = AuditOutcome.FAILURE_UNAUTHORIZED
                    elif e.status_code == 404:
                        outcome = AuditOutcome.FAILURE_NOT_FOUND
                    else:
                        outcome = AuditOutcome.FAILURE_VALIDATION
                else:
                    outcome = AuditOutcome.FAILURE_INTERNAL
                
                log_audit_background(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata={**safe_metadata, "error_type": type(e).__name__},
                    tenant_id=tenant_id,
                    request_id=request_id,
                )
                
                raise
        
        return wrapper  # type: ignore
    
    return decorator
