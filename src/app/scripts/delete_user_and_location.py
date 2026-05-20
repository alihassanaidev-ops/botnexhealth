"""
Soft-delete a user by email and hard-delete a stale InstitutionLocation.

The user is soft-deleted (deleted_at set, location_id nulled, is_active false)
to comply with the audit-log retention policy documented in
src/app/models/user.py. The partial unique index on (email) WHERE
deleted_at IS NULL leaves the email free for re-invite.

The location is hard-deleted because InstitutionLocation has no deleted_at
column. CASCADE/SET NULL on dependent FKs handles cleanup; the only FK with
NO ACTION is users.location_id, which we explicitly NULL on the targeted
user before delete.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.database import create_async_engine
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(email: str, location_id: str | None, dry_run: bool) -> None:
    if not settings.database_url:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    email = email.strip().lower()

    try:
        async with async_session() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.context_type', 'user', false), "
                    "set_config('app.role', 'SUPER_ADMIN', false), "
                    "set_config('app.user_id', :uid, false)"
                ),
                {"uid": "00000000-0000-0000-0000-000000000000"},
            )

            user_row = (
                await session.execute(
                    select(User).where(User.email == email, User.deleted_at.is_(None))
                )
            ).scalar_one_or_none()

            if user_row is None:
                logger.error(f"No active user with email {email}")
                sys.exit(1)

            resolved_location_id = location_id or user_row.location_id
            if resolved_location_id is None:
                logger.error(
                    f"User {email} has no location_id; pass --location-id explicitly."
                )
                sys.exit(1)

            location_row = (
                await session.execute(
                    select(InstitutionLocation).where(
                        InstitutionLocation.id == resolved_location_id
                    )
                )
            ).scalar_one_or_none()

            print("\n" + "=" * 80)
            print("PLAN")
            print("=" * 80)
            print(f"  user.id          = {user_row.id}")
            print(f"  user.email       = {user_row.email}")
            print(f"  user.role        = {user_row.role}")
            print(f"  user.institution = {user_row.institution_id}")
            print(f"  user.location    = {user_row.location_id}")
            print(f"  target location  = {resolved_location_id}")
            if location_row is not None:
                print(f"  location.name    = {location_row.name!r}")
                print(f"  location.inst    = {location_row.institution_id}")
            else:
                print("  location.name    = (not found — nothing to delete)")
            print("=" * 80)

            if dry_run:
                print("DRY RUN — no changes made.")
                return

            user_row.deleted_at = datetime.now(timezone.utc)
            user_row.location_id = None
            user_row.is_active = False
            await session.flush()

            if location_row is not None:
                await session.delete(location_row)
                await session.flush()

            await session.commit()

            print("\nDONE")
            print(f"  soft-deleted user {email} (id={user_row.id})")
            if location_row is not None:
                print(f"  hard-deleted location id={resolved_location_id}")
            print("Email is now free to re-invite to the correct location.\n")

    except Exception as exc:
        logger.exception(f"Failed: {exc}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--location-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.location_id, args.dry_run))
