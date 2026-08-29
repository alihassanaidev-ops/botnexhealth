"""Resolve configured SMS notification recipients for no-PMS alerts."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.external_sms_notification_recipient import (
    ExternalSmsNotificationRecipient,
)
from src.app.services.sms_privacy import normalize_phone


def unique_phone_numbers(phone_numbers: list[str | None]) -> list[str]:
    """Normalize, de-duplicate, and preserve insertion order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in phone_numbers:
        normalized = normalize_phone(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


async def resolve_sms_notification_recipients(
    session: AsyncSession,
    *,
    institution_id: str,
    notification_type: str,
    location_id: str | None = None,
) -> list[str]:
    """Return active external phone numbers for a no-PMS notification type.

    Location scoping mirrors ``resolve_staff_recipients`` for email: a
    recipient with ``location_id IS NULL`` is institution-wide and always
    included, while a location-bound recipient is included only for calls at
    that location. Passing no ``location_id`` returns institution-wide
    recipients only — a call we can't place must not fan out to every site.
    """
    scope = ExternalSmsNotificationRecipient.location_id.is_(None)
    if location_id:
        scope = or_(scope, ExternalSmsNotificationRecipient.location_id == location_id)

    result = await session.execute(
        select(ExternalSmsNotificationRecipient).where(
            ExternalSmsNotificationRecipient.institution_id == institution_id,
            ExternalSmsNotificationRecipient.notification_type == notification_type,
            ExternalSmsNotificationRecipient.is_active.is_(True),
            scope,
        )
    )
    return unique_phone_numbers([row.phone_number for row in result.scalars().all()])
