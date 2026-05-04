"""Local invite flow helpers for institution and admin user creation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.user import InviteStatus, User
from src.app.services.auth_email_service import AuthEmailService
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import RefreshTokenService

logger = logging.getLogger(__name__)


class UserInviteService:
    """Create and re-arm locally managed user invites."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        email_service: AuthEmailService | None = None,
    ) -> None:
        self.session = session
        self.email_service = email_service or AuthEmailService()

    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalize invite emails before persistence and comparison."""
        return email.strip().lower()

    async def create_invited_user(
        self,
        *,
        email: str,
        role: str,
        institution_id: str | None,
        location_id: str | None = None,
        redirect_url: str | None = None,
        is_active: bool = True,
    ) -> User:
        """Create a new pending user and send an invite email."""
        user = User(
            id=str(uuid4()),
            email=self.normalize_email(email),
            role=role,
            institution_id=institution_id,
            location_id=location_id,
            is_active=is_active,
        )
        self.session.add(user)
        await self._prepare_and_send_invite(user=user, redirect_url=redirect_url)
        return user

    async def reinvite_user(
        self,
        user: User,
        *,
        redirect_url: str | None = None,
    ) -> User:
        """Rotate invite state for an existing user and send a new invite email."""
        user.email = self.normalize_email(user.email)
        await self._prepare_and_send_invite(user=user, redirect_url=redirect_url)
        try:
            await RefreshTokenService.revoke_all_for_user(user.id)
            await RefreshTokenService.revoke_all_access_tokens_for_user(user.id)
        except Exception as e:
            logger.warning("Failed to revoke sessions during reinvite for %s: %s", user.id, e)
        return user

    async def _prepare_and_send_invite(
        self,
        *,
        user: User,
        redirect_url: str | None,
    ) -> None:
        token = PasswordService.generate_one_time_token()
        now = datetime.now(timezone.utc)

        user.invite_status = InviteStatus.PENDING.value
        user.password_hash = None
        user.password_set_at = None
        user.invite_token_hash = PasswordService.hash_token(token)
        user.invite_expires_at = now + timedelta(hours=settings.invite_token_ttl_hours)
        user.password_reset_token_hash = None
        user.password_reset_expires_at = None
        user.failed_login_attempts = 0
        user.locked_until = None

        await self.session.flush()
        # Best-effort email send. The invite token is the durable artifact —
        # if the email provider is misconfigured (RESEND_API_KEY missing) or
        # transiently down, the user record + token are still persisted, and
        # an admin can trigger /reinvite to retry. Failing the entire
        # transaction here would block onboarding on a non-security boundary
        # and previously caused HTTP 500 leaks for valid recipients.
        try:
            await self.email_service.send_invite_email(
                email=user.email,
                token=token,
                redirect_url=redirect_url,
            )
        except Exception as e:
            logger.error(
                "send_invite_email failed for user_id=%s email=%s — invite "
                "token persisted, admin should /reinvite. error=%s",
                user.id, user.email, e, exc_info=True,
            )
