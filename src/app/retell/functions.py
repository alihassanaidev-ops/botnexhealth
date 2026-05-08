"""Retell AI function calling endpoint and handler registry.

HIPAA Compliance:
- Only hashed call_ids appear in logs; never raw identifiers.
- Function arguments (which may contain PHI) are never logged.
- Signature verification ensures requests originate from Retell.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.app.retell.idempotency import IDEMPOTENT_FUNCTIONS, run_with_idempotency
from src.app.retell.models import FunctionCallRequest, FunctionCallResponse, FunctionError
from src.app.retell.security import get_retell_secret, get_signature_dependency, hash_for_logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

router = APIRouter(prefix="/retell", tags=["Retell AI"])

# Per-task call context — module-global dict was unsafe under concurrent calls.
_call_context_var: ContextVar[dict[str, Any]] = ContextVar("retell_call_context", default={})

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
    return dict(_call_context_var.get())


def set_call_context(context: dict[str, Any]) -> Any:
    """Set the current call context. Returns a token for resetting."""
    return _call_context_var.set(dict(context))


def update_call_context(**fields: Any) -> Any:
    """Merge fields into the current call context. Returns a reset token."""
    merged = dict(_call_context_var.get())
    merged.update(fields)
    return _call_context_var.set(merged)


def reset_call_context(token: Any) -> None:
    _call_context_var.reset(token)


async def get_institution_from_call_context() -> tuple[Optional["Institution"], Optional["InstitutionLocation"]]:
    """
    Resolve institution and location from current call context using agent_id.

    Since each Retell agent is mapped 1:1 to an InstitutionLocation, this
    provides automatic location routing — no need for the agent to
    call list_locations or pass location_id.

    Resolution order:
    1. InstitutionLocation with matching retell_agent_id  -> (institution, location)
    2. Not found -> (None, None)
    """
    from src.app.services.institution_service import InstitutionService
    from src.app.database import get_system_db_session

    context = _call_context_var.get()
    agent_id = context.get("agent_id")
    if not agent_id:
        logger.warning("Institution resolution failed: no agent_id in call context")
        return None, None

    try:
        async with get_system_db_session(
            "retell_lookup",
            external_id=str(agent_id),
        ) as session:
            institution_service = InstitutionService(session)

            result = await institution_service.get_location_by_retell_agent_id(agent_id)
            if result:
                location, institution = result
                logger.info(
                    f"Resolved institution={hash_for_logging(institution.id)}, "
                    f"location={location.slug} from agent_id"
                )
                return institution, location

            logger.warning("Institution resolution failed: no location found for agent_id")
            return None, None
    except Exception as e:
        logger.error(f"Institution resolution error: {e}")
        return None, None


# Create signature verification dependency
verify_signature = get_signature_dependency(get_retell_secret)


def _string_value(value: Any) -> str | None:
    """Return a non-empty string value, ignoring FastAPI Param defaults."""
    if isinstance(value, str) and value:
        return value
    return None


@router.post("/functions", response_model=FunctionCallResponse)
async def handle_function_call(
    function_name: str | None = Query(None, alias="name"),
    call_id: str | None = Query(None),
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
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid function payload")

        query_function_name = _string_value(function_name)
        query_call_id = _string_value(call_id)

        # Allow specifying function name via query param, Retell's standard
        # body `name`, or legacy `function_name`.
        resolved_function_name = (
            _string_value(payload.get("function_name"))
            or _string_value(payload.get("name"))
            or query_function_name
        )

        # Retell's "Payload: args only" mode sends only the argument object.
        # If the function name came from the query string and no wrapper fields
        # are present, wrap the body into our internal request shape.
        wrapper_keys = {"args", "call", "chat", "call_id", "function_name", "name"}
        if (
            query_function_name
            and "args" not in payload
            and not any(key in payload for key in wrapper_keys)
        ):
            payload = {
                "function_name": query_function_name,
                "call_id": query_call_id,
                "args": payload,
            }
        else:
            if "function_name" not in payload and resolved_function_name:
                payload["function_name"] = resolved_function_name
            if "call_id" not in payload and query_call_id:
                payload["call_id"] = query_call_id

        request = FunctionCallRequest.model_validate(payload)

        # Handle call_id extraction
        if not request.call_id and request.chat:
            request.call_id = request.chat.get("call_id")
        if not request.call_id and isinstance(payload.get("call"), dict):
            request.call_id = payload["call"].get("call_id")
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

        # Set call context for institution resolution in handlers.
        # agent_id may be at the top level or nested under "call".
        # Stored in a ContextVar so concurrent calls don't share state.
        token = _call_context_var.set({
            "call_id": request.call_id,
            "agent_id": (
                payload.get("agent_id")
                or payload.get("call", {}).get("agent_id")
            ),
            "args": request.args,
        })

        try:
            # Execute the function (idempotent for mutating functions)
            if request.function_name in IDEMPOTENT_FUNCTIONS:
                result = await run_with_idempotency(
                    handler,
                    function_name=request.function_name,
                    call_id=request.call_id,
                    args=request.args,
                )
            else:
                result = await handler(request.args)

            # Log success (no PHI in result logging)
            logger.info(f"Function completed: call={call_id_hash}, function={request.function_name}")

            return FunctionCallResponse(result=result)
        finally:
            _call_context_var.reset(token)

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
