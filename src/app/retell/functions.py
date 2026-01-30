"""Retell AI function calling endpoint and handler registry."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine

from fastapi import APIRouter, Depends, HTTPException, Query

from src.app.config import settings
from src.app.retell.models import FunctionCallRequest, FunctionCallResponse, FunctionError
from src.app.retell.security import get_signature_dependency, hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retell", tags=["Retell AI"])

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


def get_retell_secret() -> str | None:
    """Get Retell secret from settings."""
    return getattr(settings, "retell_api_secret", None)


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
        logger.info(f"Retell Payload: {json.dumps(payload)}")  # DEBUG: Inspect structure

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
        
        # Execute the function
        result = await handler(request.args)
        
        # Log success (no PHI in result logging)
        logger.info(f"Function completed: call={call_id_hash}, function={request.function_name}")
        
        return FunctionCallResponse(result=result)
        
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
