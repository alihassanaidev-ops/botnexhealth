"""Staff email recipient resolution, shared by call notifications and workflows.

Extracted from ``tasks.notifications`` so the automation ``send_email`` node can
reach the same RBAC-aware recipient list without duplicating it.

The global ``RESEND_ALERT_RECIPIENTS`` fallback deliberately stays in the caller
rather than moving here: it is a cross-tenant operator safety net for call
alerts, and a tenant-scoped workflow must not fan out to it.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.external_notification_recipient import ExternalNotificationRecipient
from src.app.models.user import InviteStatus, User, UserRole
from src.app.models.user_email_notification_preference import (
    UserEmailNotificationPreference,
)

# Roles that only receive notifications for their own location.
_LOCATION_SCOPED_ROLES = (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value)


def unique_emails(emails: list[str]) -> list[str]:
    """Lowercase, strip and de-duplicate while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email = (raw or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


async def resolve_staff_recipients(
    session: AsyncSession,
    *,
    institution_id: str,
    location_id: str | None = None,
    notification_type: str | None = None,
    include_external: bool = True,
) -> list[str]:
    """Resolve the staff email addresses for an institution/location.

    Includes institution admins, plus location admins and staff bound to
    ``location_id`` when one is given. Users who opted out of
    ``notification_type`` are excluded, and active external recipients for that
    type are added when ``include_external`` is set.

    Note that ``GROUP_ADMIN`` is never included: that role is read-only
    oversight across an InstitutionGroup and is deliberately kept off routes
    carrying patient information.
    """
    filters = [
        User.institution_id == institution_id,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
        User.invite_status == InviteStatus.ACCEPTED.value,
    ]

    role_scope = [User.role == UserRole.INSTITUTION_ADMIN.value]
    if location_id:
        role_scope.append(
            and_(
                User.location_id == location_id,
                User.role.in_(_LOCATION_SCOPED_ROLES),
            )
        )

    user_query = select(User.email).where(*filters).where(or_(*role_scope))

    if notification_type:
        opted_out = (
            select(UserEmailNotificationPreference.user_id).where(
                UserEmailNotificationPreference.template_type == notification_type,
                UserEmailNotificationPreference.is_enabled.is_(False),
            )
        ).scalar_subquery()
        user_query = user_query.where(User.id.not_in(opted_out))

    result = await session.execute(user_query)
    emails = [row[0] for row in result.all() if row and row[0]]

    if include_external and notification_type:
        ext_result = await session.execute(
            select(ExternalNotificationRecipient.email).where(
                ExternalNotificationRecipient.institution_id == institution_id,
                ExternalNotificationRecipient.template_type == notification_type,
                ExternalNotificationRecipient.is_active.is_(True),
            )
        )
        emails.extend(row[0] for row in ext_result.all() if row and row[0])

    return unique_emails(emails)
