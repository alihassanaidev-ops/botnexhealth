"""Institution service for CRUD operations and lookups."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.sms_privacy import hash_for_logging

logger = logging.getLogger(__name__)


class InstitutionService:
    """Service for institution CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, institution_id: str) -> Institution | None:
        """Get institution by ID."""
        result = await self.session.execute(
            select(Institution).where(
                Institution.id == institution_id,
                Institution.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_slug(
        self, slug: str, include_inactive: bool = False
    ) -> Institution | None:
        """Get institution by slug."""
        query = select(Institution).where(Institution.slug == slug)
        if not include_inactive:
            query = query.where(Institution.is_active.is_(True))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_all(self, include_inactive: bool = False) -> list[Institution]:
        """List all institutions."""
        query = select(Institution)
        if not include_inactive:
            query = query.where(Institution.is_active.is_(True))
        query = query.order_by(Institution.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        name: str,
        slug: str,
        *,
        nexhealth_api_key: str | None = None,
        location_limit: int = 1,
        jurisdiction: str | None = None,
    ) -> Institution:
        """Create a new institution."""
        from src.app.models.institution import DEFAULT_JURISDICTION

        institution = Institution(
            name=name,
            slug=slug,
            location_limit=location_limit,
            jurisdiction=jurisdiction or DEFAULT_JURISDICTION.value,
        )

        # Set encrypted fields via properties
        institution.nexhealth_api_key = nexhealth_api_key

        self.session.add(institution)
        await self.session.flush()
        await self.session.refresh(institution)

        logger.info(
            "Created institution: slug=%s institution_hash=%s",
            institution.slug,
            hash_for_logging(str(institution.id)),
        )
        return institution

    async def update(self, institution: Institution, **updates: Any) -> Institution:
        """Update institution fields."""
        # Fields that use encryption setters
        encrypted_fields = {
            "nexhealth_api_key",
        }

        for key, value in updates.items():
            if key in encrypted_fields:
                # Use property setter for encrypted fields
                setattr(institution, key, value)
            elif hasattr(institution, key):
                setattr(institution, key, value)

        await self.session.flush()
        await self.session.refresh(institution)

        logger.info(f"Updated institution: {institution.slug}")
        return institution

    # =========================================================================
    # Location CRUD
    # =========================================================================

    async def create_location(
        self, institution_id: str, **data: Any
    ) -> InstitutionLocation:
        """Create a new location for an institution."""
        location = InstitutionLocation(institution_id=institution_id)

        for key, value in data.items():
            if hasattr(location, key):
                setattr(location, key, value)

        self.session.add(location)
        await self.session.flush()
        await self.session.refresh(location)
        logger.info(
            "Created location: slug=%s institution_hash=%s",
            location.slug,
            hash_for_logging(institution_id),
        )
        return location

    async def update_location(
        self, location: InstitutionLocation, **updates: Any
    ) -> InstitutionLocation:
        """Update location fields."""
        for key, value in updates.items():
            if hasattr(location, key):
                setattr(location, key, value)

        await self.session.flush()
        await self.session.refresh(location)
        logger.info(f"Updated location: {location.slug}")
        return location

    async def delete_location(
        self, location: InstitutionLocation, hard: bool = False
    ) -> None:
        """Delete a location (soft or hard)."""
        if hard:
            await self.session.delete(location)
            logger.info(f"Hard deleted location: {location.slug}")
        else:
            location.is_active = False
            await self.session.flush()
            logger.info(f"Soft deleted location: {location.slug}")

    async def list_locations(
        self, institution_id: str, include_inactive: bool = False
    ) -> list[InstitutionLocation]:
        """List locations for an institution."""
        query = select(InstitutionLocation).where(
            InstitutionLocation.institution_id == institution_id
        )
        if not include_inactive:
            query = query.where(InstitutionLocation.is_active.is_(True))
        query = query.order_by(InstitutionLocation.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_location_by_slug(
        self, slug: str, institution_id: str
    ) -> InstitutionLocation | None:
        """Get a location by slug, scoped to a specific institution.

        Slug is globally unique today, but the institution_id predicate is
        defense-in-depth: if a future migration changes slug uniqueness to
        per-institution, callers don't silently start matching wrong-tenant
        rows. For platform-admin "is this slug taken anywhere?" checks, use
        ``find_any_location_by_slug`` instead.
        """
        result = await self.session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.slug == slug,
                InstitutionLocation.institution_id == institution_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_any_location_by_slug(self, slug: str) -> InstitutionLocation | None:
        """Look up a location by slug across ALL institutions.

        Use this only for cross-tenant uniqueness checks (e.g. before
        creating a new location with a candidate slug). Routes that serve
        tenant-scoped data must use ``get_location_by_slug`` instead so the
        institution_id predicate is in the WHERE clause.
        """
        result = await self.session.execute(
            select(InstitutionLocation).where(InstitutionLocation.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_location_by_retell_agent_id(
        self, agent_id: str
    ) -> tuple[InstitutionLocation, Institution] | None:
        """Get location and its parent institution by Retell agent ID."""
        result = await self.session.execute(
            select(InstitutionLocation, Institution)
            .join(Institution, InstitutionLocation.institution_id == Institution.id)
            .where(
                InstitutionLocation.retell_agent_id == agent_id,
                InstitutionLocation.is_active.is_(True),
                Institution.is_active.is_(True),
            )
        )
        row = result.first()
        if row:
            return row[0], row[1]
        return None

    # =========================================================================
    # Institution deletion (kept below location methods)
    # =========================================================================

    async def delete(
        self,
        institution: Institution,
        hard_delete: bool = False,
    ) -> None:
        """
        Delete an institution.

        Args:
            institution: Institution to delete
            hard_delete: If True, permanently delete. If False, soft delete (set is_active=False).
        """
        if hard_delete:
            from src.app.models.user import User

            result = await self.session.execute(
                select(User).where(User.institution_id == institution.id)
            )
            users = result.scalars().all()

            for user in users:
                await self.session.delete(user)
                logger.info(
                    "Deleted user associated with institution: user_hash=%s institution_slug=%s",
                    hash_for_logging(str(user.id)),
                    institution.slug,
                )

            # Flush to ensure user deletes are committed before institution delete
            await self.session.flush()

            await self.session.delete(institution)
            logger.info(f"Hard deleted institution: {institution.slug}")
        else:
            institution.is_active = False
            await self.session.flush()
            logger.info(f"Soft deleted institution: {institution.slug}")
