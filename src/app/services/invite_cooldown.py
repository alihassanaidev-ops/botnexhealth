"""Invite cooldown helpers for admin invite actions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.user import User, UserRole

INVITE_COOLDOWN_BASE_SECONDS = 30
INVITE_COOLDOWN_MAX_EXPONENT = 6  # 30s * 2^6 = 32 minutes max
INVITE_COOLDOWN_DECAY_SECONDS = 10 * 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_invite_cooldown(session: AsyncSession, current_user: User) -> User:
    """
    Enforce invite cooldown for non-super admins.

    Returns a session-bound user instance for updates.
    """
    if current_user.role == UserRole.SUPER_ADMIN.value:
        return current_user

    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        return current_user

    now = _now_utc()
    if user.invite_cooldown_until and now < user.invite_cooldown_until:
        retry_after = int((user.invite_cooldown_until - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Invite cooldown active. Try again in {retry_after}s.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )

    return user


def apply_invite_cooldown(user: User, now: datetime | None = None) -> None:
    """
    Apply exponential cooldown after a successful invite.

    The exponent increases when invites are sent within DECAY window,
    otherwise it resets to base.
    """
    if user.role == UserRole.SUPER_ADMIN.value:
        return

    now = now or _now_utc()
    last_invite_at = user.last_invite_at
    current_exponent = user.invite_cooldown_exponent or 0

    if last_invite_at and (now - last_invite_at).total_seconds() < INVITE_COOLDOWN_DECAY_SECONDS:
        next_exponent = min(current_exponent + 1, INVITE_COOLDOWN_MAX_EXPONENT)
    else:
        next_exponent = 0

    cooldown_seconds = INVITE_COOLDOWN_BASE_SECONDS * (2 ** next_exponent)

    user.invite_cooldown_exponent = next_exponent
    user.invite_cooldown_until = now + timedelta(seconds=cooldown_seconds)
    user.last_invite_at = now
