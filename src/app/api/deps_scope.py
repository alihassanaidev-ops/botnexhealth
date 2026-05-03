"""Dependencies for tenant and location scoping."""

from __future__ import annotations

from collections.abc import Callable
from json import JSONDecodeError
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from src.app.api.deps import get_current_institution_or_location_user
from src.app.database import get_db_session
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
    if not current_user.location_id or str(current_user.location_id) != str(location_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this location",
        )


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
