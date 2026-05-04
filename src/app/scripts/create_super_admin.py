import asyncio
import logging
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(email: str, password: str) -> None:
    if not settings.database_url:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_session() as session:
            # Set SUPER_ADMIN RLS context so this raw session can see/insert
            # rows under FORCE ROW LEVEL SECURITY. Zero-UUID is the recognized
            # system bootstrap identity.
            await session.execute(
                text(
                    "SELECT set_config('app.context_type', 'user', false), "
                    "set_config('app.role', 'SUPER_ADMIN', false), "
                    "set_config('app.user_id', :uid, false)"
                ),
                {"uid": "00000000-0000-0000-0000-000000000000"},
            )
            result = await session.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            )
            user = result.scalar_one_or_none()

            if user:
                logger.info(f"User {email} already exists. Updating to SUPER_ADMIN and setting password.")
                user.role = UserRole.SUPER_ADMIN.value
                user.password_hash = PasswordService.hash_password(password)
                user.is_active = True
                user.invite_status = InviteStatus.ACCEPTED.value
                await session.commit()
                logger.info("Update successful.")
            else:
                user = User(
                    email=email,
                    role=UserRole.SUPER_ADMIN.value,
                    password_hash=PasswordService.hash_password(password),
                    is_active=True,
                    invite_status=InviteStatus.ACCEPTED.value,
                )
                session.add(user)
                await session.commit()
                logger.info(f"Created new SUPER_ADMIN user: {email}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m src.app.scripts.create_super_admin <email> <password>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1], sys.argv[2]))
