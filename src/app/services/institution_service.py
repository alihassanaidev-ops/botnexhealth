"""Institution service for CRUD operations and lookups."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation

logger = logging.getLogger(__name__)


class InstitutionService:
    """Service for institution CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, institution_id: str) -> Institution | None:
        """Get institution by ID."""
        result = await self.session.execute(
            select(Institution).where(Institution.id == institution_id, Institution.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str, include_inactive: bool = False) -> Institution | None:
        """Get institution by slug."""
        query = select(Institution).where(Institution.slug == slug)
        if not include_inactive:
            query = query.where(Institution.is_active == True)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_all(self, include_inactive: bool = False) -> list[Institution]:
        """List all institutions."""
        query = select(Institution)
        if not include_inactive:
            query = query.where(Institution.is_active == True)
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
    ) -> Institution:
        """Create a new institution."""
        institution = Institution(
            name=name,
            slug=slug,
            location_limit=location_limit,
        )

        # Set encrypted fields via properties
        institution.nexhealth_api_key = nexhealth_api_key

        self.session.add(institution)
        await self.session.flush()
        await self.session.refresh(institution)

        logger.info(f"Created institution: {institution.slug} (id={institution.id})")
        return institution

    async def update(
        self,
        institution: Institution,
        **updates: Any
    ) -> Institution:
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

    async def create_location(self, institution_id: str, **data: Any) -> InstitutionLocation:
        """Create a new location for an institution."""
        location = InstitutionLocation(institution_id=institution_id)

        for key, value in data.items():
            if hasattr(location, key):
                setattr(location, key, value)

        self.session.add(location)
        await self.session.flush()
        await self.session.refresh(location)
        logger.info(f"Created location: {location.slug} for institution {institution_id}")
        return location

    async def update_location(self, location: InstitutionLocation, **updates: Any) -> InstitutionLocation:
        """Update location fields."""
        for key, value in updates.items():
            if hasattr(location, key):
                setattr(location, key, value)

        await self.session.flush()
        await self.session.refresh(location)
        logger.info(f"Updated location: {location.slug}")
        return location

    async def delete_location(self, location: InstitutionLocation, hard: bool = False) -> None:
        """Delete a location (soft or hard)."""
        if hard:
            await self.session.delete(location)
            logger.info(f"Hard deleted location: {location.slug}")
        else:
            location.is_active = False
            await self.session.flush()
            logger.info(f"Soft deleted location: {location.slug}")

    async def list_locations(self, institution_id: str, include_inactive: bool = False) -> list[InstitutionLocation]:
        """List locations for an institution."""
        query = select(InstitutionLocation).where(InstitutionLocation.institution_id == institution_id)
        if not include_inactive:
            query = query.where(InstitutionLocation.is_active == True)
        query = query.order_by(InstitutionLocation.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_location_by_slug(self, slug: str) -> InstitutionLocation | None:
        """Get location by slug."""
        result = await self.session.execute(
            select(InstitutionLocation).where(InstitutionLocation.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_location_by_retell_agent_id(self, agent_id: str) -> tuple[InstitutionLocation, Institution] | None:
        """Get location and its parent institution by Retell agent ID."""
        result = await self.session.execute(
            select(InstitutionLocation, Institution)
            .join(Institution, InstitutionLocation.institution_id == Institution.id)
            .where(
                InstitutionLocation.retell_agent_id == agent_id,
                InstitutionLocation.is_active == True,
                Institution.is_active == True,
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
        supabase_service: Any = None
    ) -> None:
        """
        Delete an institution.

        Args:
            institution: Institution to delete
            hard_delete: If True, permanently delete. If False, soft delete (set is_active=False).
            supabase_service: Optional SupabaseService instance for cleaning up Supabase auth users.
        """
        if hard_delete:
            # Delete associated users first to avoid foreign key constraint violation
            from src.app.models.user import User
            result = await self.session.execute(
                select(User).where(User.institution_id == institution.id)
            )
            users = result.scalars().all()

            for user in users:
                # Delete from Supabase auth (user.id IS the Supabase UUID)
                if supabase_service:
                    try:
                        supabase_service.delete_user(user.id)
                        logger.info(f"Deleted Supabase auth user {user.id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete Supabase user {user.id}: {e}")
                        # Continue with local deletion even if Supabase fails

                await self.session.delete(user)
                logger.info(f"Deleted user {user.id} associated with institution {institution.slug}")

            # Flush to ensure user deletes are committed before institution delete
            await self.session.flush()

            await self.session.delete(institution)
            logger.info(f"Hard deleted institution: {institution.slug}")
        else:
            institution.is_active = False
            await self.session.flush()
            logger.info(f"Soft deleted institution: {institution.slug}")
