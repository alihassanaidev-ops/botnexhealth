"""Adapter factory — picks the right PMS adapter for an institution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Query, Request

from src.app.api.deps import get_current_institution_or_location_user
from src.app.database import get_db_session
from src.app.models.user import User, UserRole
from src.app.pms.base import PMSAdapter

if TYPE_CHECKING:
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

logger = logging.getLogger(__name__)


async def get_adapter_for_institution_location(
    institution: "Institution", location: "InstitutionLocation"
) -> PMSAdapter:
    """Create a fresh PMS adapter scoped to a specific location.

    The platform shares one NexHealth account; per-clinic isolation comes
    from the location's nexhealth_subdomain + nexhealth_location_id, so a
    location is mandatory.
    """
    from src.app.config import settings

    if not settings.nexhealth_api_key:
        raise ValueError("NEXHEALTH_API_KEY is not configured")

    from src.app.pms.nexhealth.adapter import NexHealthAdapter
    adapter = await NexHealthAdapter.create(institution, location=location)

    logger.info(
        "Created %s adapter for institution '%s' location '%s'",
        adapter.source, institution.slug, location.slug,
    )
    return adapter


async def get_institution_pms(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    loc_id: str | None = Query(None, alias="location_id"),
) -> PMSAdapter:
    """FastAPI dependency — resolve institution/location from authenticated user token."""
    from sqlalchemy import select

    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

    if not current_user.institution_id:
        raise HTTPException(status_code=400, detail="User is not associated with an institution")

    scoped_location_id = str(loc_id) if loc_id else None
    path_location_id = request.path_params.get("location_id")
    if path_location_id is not None:
        path_location_id = str(path_location_id)

    if current_user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        if not current_user.location_id:
            raise HTTPException(status_code=403, detail="Location-scoped account is missing location assignment")

        user_location_id = str(current_user.location_id)
        if scoped_location_id and scoped_location_id != user_location_id:
            raise HTTPException(status_code=403, detail="Not authorized for this location")
        if path_location_id and path_location_id != user_location_id:
            raise HTTPException(status_code=403, detail="Not authorized for this location")

        scoped_location_id = user_location_id
    elif not scoped_location_id and path_location_id:
        scoped_location_id = path_location_id

    async with get_db_session() as session:
        institution = (
            await session.execute(
                select(Institution).where(
                    Institution.id == current_user.institution_id,
                    Institution.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not institution:
            raise HTTPException(status_code=404, detail="Institution not found")

        if not scoped_location_id:
            # Each location maps 1:1 to a NexHealth subdomain. Picking
            # an arbitrary "first" location for a multi-location institution
            # silently routes the admin's request into the wrong subdomain,
            # so we require location_id to be explicit.
            raise HTTPException(
                status_code=400,
                detail="location_id is required (pass ?location_id= or use a path that includes it)",
            )

        location = (
            await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == scoped_location_id,
                    InstitutionLocation.institution_id == institution.id,
                    InstitutionLocation.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

        if not location:
            raise HTTPException(status_code=404, detail="Location not found")

    # Preserve compatibility with handlers reading request.state.location/institution.
    request.state.institution = institution
    request.state.location = location

    return await get_adapter_for_institution_location(institution, location)
