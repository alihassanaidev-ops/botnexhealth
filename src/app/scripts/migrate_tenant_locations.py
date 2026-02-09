"""
One-time migration script: create TenantLocation rows from existing Tenant location fields.

For each tenant that has nexhealth_location_id or retell_agent_id set,
creates a default TenantLocation copying those fields, then optionally
triggers a sync to populate cached providers + appointment types.

Usage:
    python -m src.app.scripts.migrate_tenant_locations [--sync]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert tenant name to a location slug like 'acme-dental' -> 'acme-dental-main'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug}-main"


async def migrate(run_sync: bool = False) -> None:
    from src.app.config import settings
    from src.app.database import init_database, get_db_session
    from src.app.models.tenant import Tenant
    from src.app.models.tenant_location import TenantLocation
    from src.app.services.tenant_service import TenantService
    # Import models so tables exist
    from src.app.models import (  # noqa: F401
        TenantAppointmentType,
        TenantProvider,
    )

    init_database(settings.database_url)

    async with get_db_session() as session:
        service = TenantService(session)

        result = await session.execute(select(Tenant).where(Tenant.is_active == True))
        tenants = list(result.scalars().all())

        logger.info(f"Found {len(tenants)} active tenant(s)")

        created = 0
        for tenant in tenants:
            has_location_data = tenant.nexhealth_location_id or tenant.retell_agent_id
            if not has_location_data:
                logger.info(f"Skipping {tenant.slug}: no location data to migrate")
                continue

            # Check if a location already exists for this tenant
            existing = await service.list_locations(tenant.id, include_inactive=True)
            if existing:
                logger.info(f"Skipping {tenant.slug}: already has {len(existing)} location(s)")
                continue

            loc_slug = _slugify(tenant.name)

            # Ensure slug uniqueness
            existing_slug = await service.get_location_by_slug(loc_slug)
            if existing_slug:
                loc_slug = f"{loc_slug}-{tenant.slug}"

            location = await service.create_location(
                tenant.id,
                name=f"{tenant.name} - Main",
                slug=loc_slug,
                nexhealth_subdomain=tenant.nexhealth_subdomain,
                nexhealth_location_id=tenant.nexhealth_location_id,
                retell_agent_id=tenant.retell_agent_id,
                retell_api_secret=tenant.retell_api_secret,
            )

            created += 1
            logger.info(f"Created location '{location.slug}' for tenant '{tenant.slug}'")

            if run_sync:
                from src.app.services.sync_service import SyncService
                sync_service = SyncService(session)
                try:
                    result = await sync_service.sync_location(tenant, location)
                    logger.info(
                        f"  Synced: {result.providers_synced} providers, "
                        f"{result.appointment_types_synced} appt types"
                    )
                except Exception as e:
                    logger.warning(f"  Sync failed for {location.slug}: {e}")

    logger.info(f"Migration complete: {created} location(s) created")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate tenant location fields to TenantLocation rows")
    parser.add_argument("--sync", action="store_true", help="Trigger PMS sync after creating locations")
    args = parser.parse_args()
    asyncio.run(migrate(run_sync=args.sync))


if __name__ == "__main__":
    main()
