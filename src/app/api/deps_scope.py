"""Dependencies for tenant and location scoping.

A location-scoped user (LOCATION_ADMIN / STAFF) may be assigned several
locations: the primary on ``users.location_id`` plus extra ``user_locations``
rows. Authorization is therefore a *membership* check against
``User.allowed_location_ids``, and once a request's location is validated it
must also be **bound** into the RLS context (:func:`bind_active_location`) —
authentication pins the session to the primary location, so without the
rebind the database would silently filter a request aimed at a secondary
location down to primary-location rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from json import JSONDecodeError
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from src.app.api.deps import get_current_institution_or_location_user
from src.app.database import (
    apply_rls_context,
    current_rls_context,
    get_db_session,
    set_current_rls_context,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User, UserRole

_LOCATION_SCOPED_ROLES = {
    UserRole.LOCATION_ADMIN.value,
    UserRole.STAFF.value,
}
_SLUG_FIELDS = {"loc_slug", "location_slug"}


def assert_location_scope(current_user: User, location_id: str | None) -> None:
    if current_user.role not in _LOCATION_SCOPED_ROLES:
        return
    if not location_id or str(location_id) not in current_user.allowed_location_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this location",
        )


def bind_active_location(
    current_user: User,
    location_id: str,
    request: Request | None = None,
) -> None:
    """Re-bind the request's RLS context to an already-validated location.

    Only call after :func:`assert_location_scope` (or an equivalent membership
    check) has passed. Sessions opened afterwards — and existing sessions after
    their next commit — carry the chosen location, so RLS row filters follow
    the location the user is acting on instead of their primary one.
    """
    context = current_rls_context()
    if context is None or context.location_id == str(location_id):
        return
    rebound = replace(context, location_id=str(location_id))
    set_current_rls_context(rebound)
    if request is not None:
        request.state.rls_context = rebound


async def bind_active_location_in_session(
    session: Any,
    current_user: User,
    location_id: str,
    request: Request | None = None,
) -> None:
    """:func:`bind_active_location`, plus immediate effect on an open session.

    The ContextVar rebind only reaches an already-open session at its next
    commit/rollback; helpers that validate a location mid-session must push
    the rebound context onto the session's connection right away or the rest
    of the transaction keeps filtering rows by the primary location.
    """
    bind_active_location(current_user, location_id, request)
    context = current_rls_context()
    if context is not None:
        await apply_rls_context(session, context)


def resolve_location_scope(
    current_user: User,
    location_id: str | None,
    request: Request | None = None,
) -> str | None:
    """Validate and activate the location a location-scoped request targets.

    For LOCATION_ADMIN / STAFF: returns the requested location when given
    (after a membership check), else the user's primary, and binds the result
    into the RLS context. Other roles get the requested value back unchanged —
    their scoping stays wherever it lives today.
    """
    if current_user.role not in _LOCATION_SCOPED_ROLES:
        return str(location_id) if location_id else None

    effective = (
        str(location_id)
        if location_id
        else (str(current_user.location_id) if current_user.location_id else None)
    )
    assert_location_scope(current_user, effective)
    bind_active_location(current_user, effective, request)
    return effective


def require_location_scope(
    location_id_field: str = "location_id",
) -> Callable[..., Any]:
    async def location_scope_dependency(
        request: Request,
        current_user: Annotated[
            User, Depends(get_current_institution_or_location_user)
        ],
    ) -> None:
        if current_user.role not in _LOCATION_SCOPED_ROLES:
            return

        field, value = await _location_value_from_request(request, location_id_field)
        if value is None:
            return

        if field in _SLUG_FIELDS:
            location_id = await _location_id_for_slug(str(value), current_user)
            if location_id is None:
                return
        else:
            location_id = str(value)

        assert_location_scope(current_user, location_id)
        bind_active_location(current_user, location_id, request)

    return location_scope_dependency


async def _location_value_from_request(
    request: Request, location_id_field: str
) -> tuple[str, Any | None]:
    if location_id_field in request.path_params:
        return location_id_field, request.path_params[location_id_field]

    if location_id_field in request.query_params:
        return location_id_field, request.query_params[location_id_field]

    body_value = await _body_field_value(request, location_id_field)
    if body_value is not None:
        return location_id_field, body_value

    for slug_field in _SLUG_FIELDS:
        if slug_field in request.path_params:
            return slug_field, request.path_params[slug_field]
        if slug_field in request.query_params:
            return slug_field, request.query_params[slug_field]
        body_value = await _body_field_value(request, slug_field)
        if body_value is not None:
            return slug_field, body_value

    return location_id_field, None


async def _body_field_value(request: Request, field: str) -> Any | None:
    try:
        body = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(body, dict):
        return None
    return body.get(field)


async def _location_id_for_slug(loc_slug: str, current_user: User) -> str | None:
    if not current_user.institution_id:
        return None

    async with get_db_session() as session:
        result = await session.execute(
            select(InstitutionLocation.id).where(
                InstitutionLocation.slug == loc_slug,
                InstitutionLocation.institution_id == current_user.institution_id,
            )
        )
        location_id = result.scalar_one_or_none()

    return str(location_id) if location_id is not None else None
