"""Retell AI function calling endpoint and handler registry."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.app.retell.models import FunctionCallRequest, FunctionCallResponse, FunctionError
from src.app.retell.security import get_retell_secret, get_signature_dependency, hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retell", tags=["Retell AI"])

# Current call context for tenant resolution
_current_call_context: dict[str, Any] = {}

# Registry of available functions
_function_registry: dict[str, Callable[..., Coroutine[Any, Any, dict[str, Any]]]] = {}


def register_function(name: str):
    """
    Decorator to register a function handler.
    
    Usage:
        @register_function("check_availability")
        async def check_availability(args: dict) -> dict:
            ...
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, dict[str, Any]]]):
        _function_registry[name] = func
        logger.info(f"Registered Retell function: {name}")
        return func
    return decorator


def get_call_context() -> dict[str, Any]:
    """Get current call context (set during function execution)."""
    return _current_call_context.copy()


async def get_tenant_from_call_context() -> tuple[Optional["Tenant"], Optional["TenantLocation"]]:
    """
    Resolve tenant (and optional location) from current call context using agent_id.

    Resolution order:
    1. TenantLocation with matching retell_agent_id  -> (tenant, location)
    2. Tenant with matching retell_agent_id (backward compat) -> (tenant, None)
    3. Not found -> (None, None)
    """
    from src.app.services.tenant_service import TenantService
    from src.app.database import get_db_session

    agent_id = _current_call_context.get("agent_id")
    logger.info(f"[TENANT_RESOLVE] agent_id from call context: {agent_id!r}")
    logger.info(f"[TENANT_RESOLVE] full call context keys: {list(_current_call_context.keys())}")
    if not agent_id:
        logger.warning("[TENANT_RESOLVE] No agent_id in call context — payload may not contain it")
        return None, None

    try:
        async with get_db_session() as session:
            tenant_service = TenantService(session)

            # Try location-level first
            result = await tenant_service.get_location_by_retell_agent_id(agent_id)
            logger.info(f"[TENANT_RESOLVE] location lookup result: {result}")
            if result:
                location, tenant = result
                logger.info(f"[TENANT_RESOLVE] Resolved tenant={tenant.id}, location={location.slug}")
                return tenant, location

            # Fallback: tenant-level agent_id (backward compat)
            tenant = await tenant_service.get_by_retell_agent_id(agent_id)
            logger.info(f"[TENANT_RESOLVE] tenant-level lookup result: {tenant}")
            if tenant:
                logger.info(f"[TENANT_RESOLVE] Resolved tenant={tenant.id} (no location)")
            else:
                logger.warning(f"[TENANT_RESOLVE] No tenant found for agent_id={agent_id!r}")
            return tenant, None
    except Exception as e:
        logger.warning(f"[TENANT_RESOLVE] Exception resolving agent_id {agent_id}: {e}")
        return None, None


# Create signature verification dependency
verify_signature = get_signature_dependency(get_retell_secret)


@router.post("/functions", response_model=FunctionCallResponse)
async def handle_function_call(
    function_name: str | None = Query(None, alias="name"),
    body: bytes = Depends(verify_signature),
) -> FunctionCallResponse | FunctionError:
    """
    Handle function calls from Retell AI.
    
    This endpoint is called synchronously during a conversation when
    the voice agent needs to execute a function (e.g., lookup patient,
    check availability, schedule appointment).
    
    HIPAA Note: We only log hashed call_ids, never function arguments
    which may contain PHI.
    """
    try:
        # Parse the verified body
        payload = json.loads(body)
        logger.debug(f"Retell Payload: {json.dumps(payload)}")

        # Allow specifying function name via query param (Standard Retell Pattern)
        # Retell often sends just { args: {...}, call_id: ... }
        if "function_name" not in payload and function_name:
            payload["function_name"] = function_name

        request = FunctionCallRequest.model_validate(payload)
        
        # Handle call_id extraction
        if not request.call_id and request.chat:
             request.call_id = request.chat.get("call_id") # Retell sometimes nests it?
        if not request.call_id:
             request.call_id = "unknown_call_id"

        # Log function call (HIPAA-safe: hash call_id, log function name only)
        call_id_hash = hash_for_logging(request.call_id)
        logger.info(
            f"Function call received: call={call_id_hash}, function={request.function_name}"
        )
        
        # Get handler from registry
        handler = _function_registry.get(request.function_name)
        if not handler:
            logger.warning(f"Unknown function: {request.function_name}")
            raise HTTPException(
                status_code=400,
                detail=f"Unknown function: {request.function_name}",
            )
        
        # Set call context for tenant resolution in handlers
        global _current_call_context
        extracted_agent_id = payload.get("agent_id") or payload.get("call", {}).get("agent_id")
        logger.info(f"[DEBUG] Payload top-level keys: {list(payload.keys())}")
        logger.info(f"[DEBUG] agent_id extracted: {extracted_agent_id!r}")
        _current_call_context = {
            "call_id": request.call_id,
            "agent_id": extracted_agent_id,
            "args": request.args,
        }
        
        try:
            # Execute the function
            result = await handler(request.args)
            
            # Log success (no PHI in result logging)
            logger.info(f"Function completed: call={call_id_hash}, function={request.function_name}")
            
            return FunctionCallResponse(result=result)
        finally:
            # Clear context after execution
            _current_call_context = {}
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in function call: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Function execution error: {e}")
        raise HTTPException(status_code=500, detail="Function execution failed")


# ============================================================================
# Import handlers to register them
# ============================================================================
# This import must be at the bottom to avoid circular imports
from src.app.retell import handlers  # noqa: F401, E402
