"""Idempotency for synchronous Retell function calls.

Mid-call function invocations (book/cancel/reschedule/create_patient) are
deduped per (call_id, function_name, args_hash). The cached result is
replayed verbatim on retry so a Retell network blip does not produce a
second booking, cancellation, or patient record.

Read-only functions are not wrapped — replays of a lookup are harmless and
not worth the storage.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.security import keyed_hash

logger = logging.getLogger(__name__)


# Functions whose retries must not produce duplicate side effects.
IDEMPOTENT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "create_patient",
    }
)


_MISSING_CALL_ID = "unknown_call_id"


def _hash_for_logging(value: str | None) -> str:
    if not value:
        return "none"
    return keyed_hash(value, purpose="retell-log-hash-v1", truncate_hex=16)


def canonical_args_hash(args: dict[str, Any]) -> str:
    """Stable, key-order-independent HMAC hash of function args."""
    if not isinstance(args, dict):
        args = {"_value": args}
    canonical = json.dumps(
        args,
        sort_keys=True,
        separators=(",", ":"),
        default=str,  # tolerate datetime/UUID/Decimal that may sneak in
    )
    return keyed_hash(canonical, purpose="retell_function_args")


def _processing_response() -> dict[str, Any]:
    """Response returned when an in-flight duplicate is detected."""
    return {
        "error": "still_processing",
        "retryable": True,
        "message": "I'm still finalizing that — give me a moment and I'll confirm.",
    }


async def _claim_invocation(
    call_id: str, function_name: str, args_hash: str
) -> tuple[str, dict[str, Any] | None]:
    """Atomically claim or look up an invocation row.

    Commits the claim before returning so a downstream rollback in the
    handler cannot erase the PROCESSING marker. Without this, a stuck-in-
    flight or stuck-failed row could come back to life on the next retry
    and re-execute the side-effect.

    Returns:
        (action, cached_result)
        action ∈ {"new", "replay_completed", "in_flight", "retry_failed"}
        cached_result is set only when action == "replay_completed".
    """
    from src.app.database import get_system_db_session
    from src.app.models.retell_function_invocation import (
        RetellFunctionInvocation,
        RetellFunctionStatus,
    )

    async with get_system_db_session(
        "retell_function",
        external_id=call_id,
    ) as session:
        existing = (
            await session.execute(
                select(RetellFunctionInvocation).where(
                    RetellFunctionInvocation.call_id == call_id,
                    RetellFunctionInvocation.function_name == function_name,
                    RetellFunctionInvocation.args_hash == args_hash,
                )
            )
        ).scalar_one_or_none()

        if existing:
            if existing.status == RetellFunctionStatus.COMPLETED.value:
                cached: dict[str, Any] | None = None
                if existing.result_json:
                    try:
                        cached = json.loads(existing.result_json)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Cached result_json malformed for %s/%s — re-running",
                            call_id,
                            function_name,
                        )
                        cached = None
                if cached is not None:
                    return "replay_completed", cached
                # Malformed cache: treat as failed and retry.
                existing.status = RetellFunctionStatus.PROCESSING.value
                existing.attempts += 1
                existing.last_error = None
                existing.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "retry_failed", None

            if existing.status == RetellFunctionStatus.PROCESSING.value:
                return "in_flight", None

            # FAILED — allow retry, increment attempts, clear error.
            existing.status = RetellFunctionStatus.PROCESSING.value
            existing.attempts += 1
            existing.last_error = None
            existing.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return "retry_failed", None

        row = RetellFunctionInvocation(
            call_id=call_id,
            function_name=function_name,
            args_hash=args_hash,
            status=RetellFunctionStatus.PROCESSING.value,
            attempts=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return "in_flight", None
        return "new", None


async def _record_outcome(
    call_id: str,
    function_name: str,
    args_hash: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    institution_id: str | None = None,
) -> None:
    """Persist the terminal outcome of an idempotent invocation.

    Commits explicitly so the COMPLETED/FAILED transition is durable even
    if a later request-handler exception rolls back the surrounding work.
    """
    from src.app.database import get_system_db_session
    from src.app.models.retell_function_invocation import RetellFunctionInvocation

    async with get_system_db_session(
        "retell_function",
        institution_id=institution_id,
        external_id=call_id,
    ) as session:
        row = (
            await session.execute(
                select(RetellFunctionInvocation).where(
                    RetellFunctionInvocation.call_id == call_id,
                    RetellFunctionInvocation.function_name == function_name,
                    RetellFunctionInvocation.args_hash == args_hash,
                )
            )
        ).scalar_one_or_none()
        if not row:
            return
        row.status = status
        row.result_json = json.dumps(result) if result is not None else None
        row.last_error = error
        if institution_id:
            row.institution_id = institution_id
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def run_with_idempotency(
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    function_name: str,
    call_id: str,
    args: dict[str, Any],
    institution_id: str | None = None,
) -> dict[str, Any]:
    """Execute `handler(args)` exactly once per (call_id, function_name, args_hash).

    Replays return the cached result. In-flight duplicates return a
    retryable error so Retell can back off without speaking false success.

    Functions in :data:`IDEMPOTENT_FUNCTIONS` are mutating side-effects —
    booking, cancellation, patient creation. Bypassing idempotency for them
    can produce duplicate bookings or duplicate patient records, so a
    missing ``call_id`` is rejected as a 400 rather than silently skipping
    the dedupe layer.
    """
    from src.app.models.retell_function_invocation import RetellFunctionStatus

    if not call_id or call_id == _MISSING_CALL_ID:
        if function_name in IDEMPOTENT_FUNCTIONS:
            logger.error(
                "Refusing idempotent function without call_id: function=%s",
                function_name,
            )
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail=f"call_id is required for {function_name}",
            )
        logger.info("Skipping idempotency for %s: no usable call_id", function_name)
        return await handler(args)

    args_hash = canonical_args_hash(args)
    action, cached = await _claim_invocation(call_id, function_name, args_hash)

    if action == "replay_completed":
        logger.info(
            "Idempotent replay served from cache: function=%s call_id_hash=%s",
            function_name,
            _hash_for_logging(call_id),
        )
        return cached  # type: ignore[return-value]

    if action == "in_flight":
        logger.info(
            "Idempotent duplicate in flight: function=%s call_id_hash=%s",
            function_name,
            _hash_for_logging(call_id),
        )
        return _processing_response()

    try:
        result = await handler(args)
    except Exception as exc:
        await _record_outcome(
            call_id,
            function_name,
            args_hash,
            status=RetellFunctionStatus.FAILED.value,
            error=str(exc),
            institution_id=institution_id,
        )
        raise

    await _record_outcome(
        call_id,
        function_name,
        args_hash,
        status=RetellFunctionStatus.COMPLETED.value,
        result=result,
        institution_id=institution_id,
    )
    return result
