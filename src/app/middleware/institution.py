"""Middleware for institution and location context resolution."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.database import get_system_db_session
from src.app.services.institution_service import InstitutionService

logger = logging.getLogger(__name__)

# Headers for institution/location identification
INSTITUTION_HEADER = "X-Institution-Slug"
LOCATION_HEADER = "X-Location-Slug"


class InstitutionMiddleware(BaseHTTPMiddleware):
    """
    Middleware to resolve institution and location from request headers.

    - X-Institution-Slug → request.state.institution
    - X-Location-Slug → request.state.location (requires institution)

    For Retell webhooks, institution/location is resolved by agent_id in the handler.
    """

    # Paths that don't require institution context
    EXEMPT_PATHS = {
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    # Paths with their own institution resolution (e.g., Retell webhooks use agent_id)
    SELF_RESOLVING_PATHS = {
        "/webhook/retell",
    }

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        """Process request and resolve institution + location context."""
        path = request.url.path

        # Skip exempt paths
        if path in self.EXEMPT_PATHS or path.startswith("/admin/"):
            return await call_next(request)

        # Skip self-resolving paths (they handle institution resolution internally)
        if any(path.startswith(p) for p in self.SELF_RESOLVING_PATHS):
            return await call_next(request)

        # Get institution slug from header
        institution_slug = request.headers.get(INSTITUTION_HEADER)

        if not institution_slug:
            # No institution header - continue without institution (handler will decide if required)
            request.state.institution = None
            request.state.location = None
            return await call_next(request)

        # Lookup institution (and optional location)
        try:
            async with get_system_db_session(
                "middleware_lookup",
                external_id=institution_slug,
            ) as session:
                service = InstitutionService(session)
                institution = await service.get_by_slug(institution_slug)

                if institution:
                    request.state.institution = institution
                    logger.debug(f"Resolved institution: {institution.slug}")

                    # Resolve location if header present
                    location_slug = request.headers.get(LOCATION_HEADER)
                    if location_slug:
                        location = await service.get_location_by_slug(
                            location_slug, institution.id
                        )
                        if location and location.is_active:
                            request.state.location = location
                            logger.debug(f"Resolved location: {location.slug}")
                        else:
                            logger.warning(
                                f"Location not found or inactive in institution "
                                f"{institution.slug}: {location_slug}"
                            )
                            request.state.location = None
                    else:
                        request.state.location = None
                else:
                    logger.warning(f"Institution not found: {institution_slug}")
                    request.state.institution = None
                    request.state.location = None
        except Exception as e:
            logger.error(f"Error resolving institution: {e}")
            request.state.institution = None
            request.state.location = None

        return await call_next(request)


def get_institution_from_request(request: Request):
    """Get institution from request state. Returns None if not set."""
    return getattr(request.state, "institution", None)


def get_location_from_request(request: Request):
    """Get location from request state. Returns None if not set."""
    return getattr(request.state, "location", None)
