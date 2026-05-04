import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.database import create_async_engine
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService
from src.app.services.auth_email_service import AuthEmailService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main(email: str, frontend_base_url: str) -> None:
    if not settings.database_url:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Normalize email
    email = email.strip().lower()

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
            # 1. Check if user already exists (only active rows; soft-deleted
            # users may share an email since the partial unique index excludes
            # them — see migration 20260505_user_email_partial_unique).
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                logger.error(f"User {email} already exists in the database.")
                sys.exit(1)

            # 2. Generate secure one-time invite token
            token = PasswordService.generate_one_time_token()
            now = datetime.now(timezone.utc)

            # 3. Create the SUPER_ADMIN user in PENDING state
            user = User(
                id=str(uuid4()),
                email=email,
                role=UserRole.SUPER_ADMIN.value,
                institution_id=None,
                location_id=None,
                is_active=True,
                invite_status=InviteStatus.PENDING.value,
                invite_token_hash=PasswordService.hash_token(token),
                invite_expires_at=now + timedelta(hours=settings.invite_token_ttl_hours),
            )
            session.add(user)
            await session.commit()

            # 4. Generate the invite URL
            redirect_url = f"{frontend_base_url.rstrip('/')}/set-password"
            email_service = AuthEmailService()
            action_url = email_service.build_action_url(
                token=token,
                flow="invite",
                redirect_url=redirect_url,
                default_path="/set-password"
            )

            print("\n" + "="*80)
            print(f"✅ SUPER ADMIN INVITE CREATED FOR: {email}")
            print("="*80)
            print("\nIf you have Resend configured, an email is being sent now.")
            print("\nOtherwise, copy and paste this secure, one-time link into your browser:")
            print(f"\n🔗  {action_url}\n")
            print("="*80 + "\n")

            # 5. Attempt to send the email
            try:
                await email_service.send_invite_email(
                    email=email,
                    token=token,
                    redirect_url=redirect_url
                )
                logger.info("Successfully sent invite email via configured provider.")
            except Exception as e:
                logger.warning(f"Could not send email (Provider likely not configured): {e}")
                logger.info("Use the link printed above to complete setup.")

    except Exception as e:
        logger.error(f"Failed to generate invite: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m src.app.scripts.invite_super_admin <email> <frontend_base_url>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1], sys.argv[2]))
