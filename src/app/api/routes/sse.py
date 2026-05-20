"""Institution-scoped Server-Sent Events endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from src.app.api.deps import get_current_active_user
from src.app.models.user import User
from src.app.services.event_bus import (
    create_sse_ticket,
    redeem_sse_ticket,
    subscribe_events,
)
from src.app.services.sms_privacy import hash_for_logging, safe_error_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution", tags=["SSE"])

HEARTBEAT_INTERVAL_SECONDS = 25.0


@router.post("/events/ticket")
async def create_event_ticket(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, str]:
    """Exchange a JWT for a short-lived single-use SSE ticket.

    The ticket is valid for 30 seconds and can only be used once.
    This avoids exposing the JWT in query strings / access logs.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )
    ticket = create_sse_ticket(
        user_id=str(current_user.id),
        institution_id=current_user.institution_id,
    )
    return {"ticket": ticket}


@router.get("/events")
async def stream_institution_events(
    request: Request,
    ticket: Annotated[str, Query()],
) -> StreamingResponse:
    """Stream institution-scoped Redis pub/sub events to the frontend.

    Authenticate via a short-lived ticket obtained from POST /events/ticket.
    """
    payload = await redeem_sse_ticket(ticket)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired ticket",
        )

    institution_id = payload["institution_id"]

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        reader_failed = asyncio.Event()

        async def reader() -> None:
            try:
                async for event in subscribe_events(institution_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "SSE subscription unavailable for institution_hash=%s error=%s",
                    hash_for_logging(institution_id),
                    safe_error_summary(exc),
                )
            finally:
                reader_failed.set()

        reader_task = asyncio.create_task(reader())

        try:
            while not await request.is_disconnected():
                if reader_failed.is_set() and queue.empty():
                    # Reader stopped (Redis error or subscription ended). Close
                    # the stream so EventSource reconnects via backoff instead
                    # of sitting on a dead connection receiving only heartbeats.
                    break

                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=HEARTBEAT_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue

                event_type = str(event.get("type") or "message")
                event_data = event.get("data")
                if not isinstance(event_data, dict):
                    event_data = {}

                timestamp = event.get("timestamp")
                if timestamp and "timestamp" not in event_data:
                    event_data = {**event_data, "timestamp": timestamp}

                encoded = json.dumps(event_data, separators=(",", ":"), default=str)
                yield f"event: {event_type}\ndata: {encoded}\n\n"
        finally:
            reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await reader_task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
