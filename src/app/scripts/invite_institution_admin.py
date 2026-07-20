"""One-off: invite an INSTITUTION_ADMIN for a specific institution.

Mirrors :mod:`invite_super_admin` but seats an institution-scoped admin — used
to bootstrap the first admin of an institution (before anyone exists who could
invite via the dashboard). Runs on the deployed image as a one-off ECS task, so
no new deploy is required beyond having this script in the image.

Usage:
    python -m src.app.scripts.invite_institution_admin <email> <institution_id> <frontend_base_url>
"""

import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.database import create_async_engine
from src.app.models.institution import Institution
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.user_invite_service import UserInviteService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(email: str, institution_id: str, frontend_base_url: str) -> None:
    if not settings.database_url:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    try:
        UUID(institution_id)  # reject a malformed id before touching the DB
    except ValueError:
        logger.error("institution_id %r is not a valid UUID.", institution_id)
        sys.exit(1)

    email = email.strip().lower()
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_session() as session:
            # SUPER_ADMIN RLS context so this raw session can read/insert under
            # FORCE ROW LEVEL SECURITY. Zero-UUID is the system bootstrap identity
            # (same pattern as invite_super_admin).
            await session.execute(
                text(
                    "SELECT set_config('app.context_type', 'user', false), "
                    "set_config('app.role', 'SUPER_ADMIN', false), "
                    "set_config('app.user_id', :uid, false)"
                ),
                {"uid": "00000000-0000-0000-0000-000000000000"},
            )

            # Institution must exist — fail loud rather than seat an admin on a
            # dangling institution_id.
            inst = (
                await session.execute(
                    select(Institution).where(Institution.id == institution_id)
                )
            ).scalar_one_or_none()
            if inst is None:
                logger.error("Institution %s not found.", institution_id)
                sys.exit(1)

            # Reject duplicates (partial unique index already excludes
            # soft-deleted rows, so only active users collide).
            existing = (
                await session.execute(
                    select(User).where(
                        User.email == email, User.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if existing:
                logger.error(
                    "User %s already exists (role=%s, institution_id=%s). "
                    "Use the reinvite flow instead.",
                    email,
                    existing.role,
                    existing.institution_id,
                )
                sys.exit(1)

            redirect_url = f"{frontend_base_url.rstrip('/')}/set-password"
            svc = UserInviteService(session)
            user = await svc.create_invited_user(
                email=email,
                role=UserRole.INSTITUTION_ADMIN.value,
                institution_id=institution_id,
                redirect_url=redirect_url,
            )
            await session.commit()

            print("\n" + "=" * 80)
            print(f"✅ INSTITUTION ADMIN INVITE CREATED FOR: {email}")
            print(f"   Institution: {inst.name} ({institution_id})")
            print(f"   User id: {user.id}  status: {InviteStatus.PENDING.value}")
            print("=" * 80)
            print("\nAn invite email is sent if Resend is configured; otherwise use")
            print("the set-password link delivered to the recipient.\n")

    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - surface the failure to the operator
        logger.error("Failed to create institution-admin invite: %s", e)
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Usage: python -m src.app.scripts.invite_institution_admin "
            "<email> <institution_id> <frontend_base_url>"
        )
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
