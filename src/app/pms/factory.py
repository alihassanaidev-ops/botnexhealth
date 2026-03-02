"""Adapter factory — picks the right PMS adapter for an institution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from src.app.pms.base import PMSAdapter

if TYPE_CHECKING:
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

logger = logging.getLogger(__name__)


async def get_adapter_for_institution(institution: "Institution") -> PMSAdapter:
    """Create a fresh PMS adapter for an institution (backward compat)."""
    adapter: PMSAdapter

    from src.app.config import settings

    if institution.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(institution)
    else:
        raise ValueError(f"No PMS configured for institution {institution.slug}")

    logger.info(f"Created {adapter.source} adapter for institution '{institution.slug}'")
    return adapter


async def get_adapter_for_institution_location(institution: "Institution", location: "InstitutionLocation") -> PMSAdapter:
    """Create a fresh PMS adapter scoped to a specific location."""
    adapter: PMSAdapter

    from src.app.config import settings

    if institution.nexhealth_api_key or settings.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        adapter = await NexHealthAdapter.create(institution, location=location)
    else:
        raise ValueError(f"No PMS configured for institution {institution.slug}")

    logger.info(f"Created {adapter.source} adapter for institution '{institution.slug}' location '{location.slug}'")
    return adapter


async def get_institution_pms(request: Request) -> PMSAdapter:
    """FastAPI dependency — resolves institution (and optional location) from request and returns adapter."""
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

    institution: Institution | None = getattr(request.state, "institution", None)
    if not institution:
        raise HTTPException(status_code=400, detail="Institution context required (X-Institution-Slug header)")

    location: InstitutionLocation | None = getattr(request.state, "location", None)
    if location:
        return await get_adapter_for_institution_location(institution, location)
    return await get_adapter_for_institution(institution)
