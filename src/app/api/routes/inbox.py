"""Shared conversation inbox routes.

One set of endpoints for five roles, with the narrowing done in
``InboxService`` rather than in each handler — so a new endpoint cannot forget a
scope check.

The group-admin split is the notable one. That role is deliberately kept off
routes carrying patient information, so it does not get the conversation
endpoints at all; it gets ``/activity``, which returns volumes and response
times and nothing that identifies a patient.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.email.inbox_service import (
    InboxAccessError,
    InboxService,
    scope_for_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbox", tags=["Inbox"])


class ThreadSummaryResponse(BaseModel):
    id: str
    channel: str
    status: str
    institution_id: str
    location_id: str | None
    contact_id: str | None
    contact_name: str | None
    contact_masked_email: str | None
    last_message_at: datetime | None
    opened_at: datetime | None
    unresolved_handoffs: int
    assignee_user_id: str | None
    latest_intent: str | None
    #: The most recent reply came from an address we did not mail — a forwarded
    #: copy or a shared mailbox. Identity must not be assumed from the thread.
    sender_mismatch: bool


class ThreadListResponse(BaseModel):
    threads: list[ThreadSummaryResponse]


class ThreadMessageResponse(BaseModel):
    id: str
    direction: str
    channel: str
    body: str | None
    subject: str | None
    intent: str | None
    created_at: datetime | None
    from_masked: str | None
    sender_mismatch: bool


class ThreadDetailResponse(BaseModel):
    thread: ThreadSummaryResponse
    messages: list[ThreadMessageResponse]


class AssignRequest(BaseModel):
    #: None clears the assignment and returns the conversation to the queue.
    assignee_user_id: str | None = None


class ResolveRequest(BaseModel):
    outcome: str | None = Field(default=None, max_length=80)


def _forbidden(exc: InboxAccessError) -> HTTPException:
    # 404 rather than 403 for a thread outside the caller's scope: confirming
    # that a conversation exists in another clinic is itself a disclosure.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/threads", response_model=ThreadListResponse)
@limiter.limit(RATE_READ)
async def list_threads(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    channel: str | None = Query(default=None, pattern="^(sms|email)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    institution_id: str | None = None,
    location_id: str | None = None,
    assigned_to: str | None = None,
    unresolved_only: bool = False,
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ThreadListResponse:
    scope = scope_for_user(current_user)
    async with get_db_session() as session:
        try:
            threads = await InboxService(session).list_threads(
                scope,
                channel=channel,
                status=status_filter,
                institution_id=institution_id,
                location_id=location_id,
                assigned_to=assigned_to,
                unresolved_only=unresolved_only,
                since=since,
                limit=limit,
                offset=offset,
            )
        except InboxAccessError as exc:
            raise _forbidden(exc) from exc
        return ThreadListResponse(
            threads=[ThreadSummaryResponse(**vars(t)) for t in threads]
        )


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponse)
@limiter.limit(RATE_READ)
async def get_thread(
    request: Request,
    thread_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ThreadDetailResponse:
    scope = scope_for_user(current_user)
    async with get_db_session() as session:
        try:
            summary, messages = await InboxService(session).get_messages(
                scope, thread_id
            )
        except InboxAccessError as exc:
            raise _forbidden(exc) from exc
        return ThreadDetailResponse(
            thread=ThreadSummaryResponse(**vars(summary)),
            messages=[ThreadMessageResponse(**vars(m)) for m in messages],
        )


@router.post("/threads/{thread_id}/assign")
@limiter.limit(RATE_WRITE)
async def assign_thread(
    request: Request,
    thread_id: str,
    body: AssignRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    scope = scope_for_user(current_user)
    async with get_db_session() as session:
        try:
            updated = await InboxService(session).assign(
                scope, thread_id, body.assignee_user_id
            )
        except InboxAccessError as exc:
            raise _forbidden(exc) from exc
        return {"updated": updated}


@router.post("/threads/{thread_id}/resolve")
@limiter.limit(RATE_WRITE)
async def resolve_thread(
    request: Request,
    thread_id: str,
    body: ResolveRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    scope = scope_for_user(current_user)
    async with get_db_session() as session:
        try:
            resolved = await InboxService(session).resolve(
                scope, thread_id, outcome=body.outcome
            )
        except InboxAccessError as exc:
            raise _forbidden(exc) from exc
        return {"resolved_handoffs": resolved}


@router.get("/activity")
@limiter.limit(RATE_READ)
async def inbox_activity(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Volumes and response times, scoped to the caller.

    This is the whole surface a group admin gets: counts and timings per clinic
    and location, with no message content, subjects, names or addresses.
    """
    scope = scope_for_user(current_user)
    async with get_db_session() as session:
        return await InboxService(session).activity(scope, days=days)
