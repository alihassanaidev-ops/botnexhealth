"""Service for creating and querying in-app notifications."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.call import Call, CallStatus
from src.app.models.notification import Notification, NotificationType
from src.app.models.user import InviteStatus, User, UserRole

logger = logging.getLogger(__name__)

# Tags that map to urgent notifications
_URGENT_TAGS = frozenset({CallStatus.EMERGENCY.value, CallStatus.COMPLAINT.value})


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _determine_notification_type(
    call_status: str | None,
    call_tags_csv: str | None,
) -> NotificationType:
    """Map call status/tags to a notification type."""
    tags = _split_csv(call_tags_csv)

    # Urgent takes priority
    if call_status in _URGENT_TAGS or any(t in _URGENT_TAGS for t in tags):
        return NotificationType.URGENT

    # Appointment booked
    if call_status == CallStatus.APPOINTMENT_BOOKED.value or CallStatus.APPOINTMENT_BOOKED.value in tags:
        return NotificationType.APPOINTMENT_BOOKED

    # Needs callback
    if call_status == CallStatus.NEEDS_CALLBACK.value or CallStatus.NEEDS_CALLBACK.value in tags:
        return NotificationType.CALLBACK_ITEM

    # Default: new call
    return NotificationType.NEW_CALL


class NotificationService:
    """Manages in-app notification creation and queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Recipient resolution ──────────────────────────────────────────

    async def _resolve_recipient_users(
        self,
        institution_id: str,
        location_id: str | None,
    ) -> list[User]:
        """Resolve users who should receive a notification.

        Logic mirrors ``_resolve_recipients`` in the email notification task
        but returns full User objects instead of email addresses.

        - INSTITUTION_ADMIN: always included (sees all locations).
        - LOCATION_ADMIN / STAFF: included only when their location_id matches.
        """
        filters = [
            User.institution_id == institution_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.invite_status == InviteStatus.ACCEPTED.value,
        ]

        scoped_location_roles = [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value]
        role_scope = [User.role == UserRole.INSTITUTION_ADMIN.value]
        if location_id:
            role_scope.append(
                and_(
                    User.location_id == location_id,
                    User.role.in_(scoped_location_roles),
                )
            )

        result = await self.session.execute(
            select(User).where(*filters).where(or_(*role_scope))
        )
        return list(result.scalars().all())

    # ── Creation ──────────────────────────────────────────────────────

    async def create_notifications_for_call(
        self,
        institution_id: str,
        location_id: str | None,
        call: Call,
        call_status: str | None,
        call_tags_csv: str | None,
    ) -> int:
        """Create one notification per recipient user for a processed call.

        Returns the count of notifications created.
        """
        notification_type = _determine_notification_type(call_status, call_tags_csv)
        recipients = await self._resolve_recipient_users(institution_id, location_id)

        if not recipients:
            logger.warning(
                "No notification recipients for call=%s institution=%s",
                call.id,
                institution_id,
            )
            return 0

        # Build title / message from call data
        caller_name = call.contact.full_name if call.contact else "Unknown caller"
        title = f"New call from {caller_name}"
        if notification_type == NotificationType.URGENT:
            title = f"URGENT: {title}"
        elif notification_type == NotificationType.APPOINTMENT_BOOKED:
            title = f"Appointment booked - {caller_name}"
        elif notification_type == NotificationType.CALLBACK_ITEM:
            title = f"Callback needed - {caller_name}"

        message = (call.summary or "").strip() or "No summary available."

        data_payload: dict[str, Any] = {"call_id": call.id}
        if call.call_status:
            data_payload["call_status"] = call.call_status
        if call.contact_id:
            data_payload["contact_id"] = call.contact_id

        for user in recipients:
            notification = Notification(
                institution_id=institution_id,
                user_id=user.id,
                type=notification_type.value,
                title=title,
                message=message,
                data=data_payload,
            )
            self.session.add(notification)

        await self.session.flush()
        count = len(recipients)
        logger.info(
            "Created %d in-app notifications: call=%s type=%s institution=%s",
            count,
            call.id,
            notification_type.value,
            institution_id,
        )
        return count

    async def create_notification(
        self,
        *,
        institution_id: str,
        user_id: str,
        notification_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> Notification:
        """Create a single notification for a specific user."""
        notification = Notification(
            institution_id=institution_id,
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            data=data,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    # ── Bulk creation for all recipients ──────────────────────────────

    async def create_bulk_notifications(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        notification_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Create notifications for all eligible recipients.

        Used by the Celery task when call object is not available in session.
        Returns count of notifications created.
        """
        recipients = await self._resolve_recipient_users(institution_id, location_id)
        if not recipients:
            return 0

        for user in recipients:
            notification = Notification(
                institution_id=institution_id,
                user_id=user.id,
                type=notification_type,
                title=title,
                message=message,
                data=data,
            )
            self.session.add(notification)

        await self.session.flush()
        return len(recipients)

    # ── Queries ───────────────────────────────────────────────────────

    async def get_notifications(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        """Return paginated notifications for a user, newest first."""
        conditions = [Notification.user_id == user_id]

        total: int = (
            await self.session.execute(
                select(func.count(Notification.id)).where(*conditions)
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                select(Notification)
                .where(*conditions)
                .order_by(Notification.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return list(rows), total

    async def get_unread_counts(self, user_id: str) -> dict[str, int]:
        """Return unread notification counts broken down by type.

        Uses conditional aggregation for a single round-trip.
        Returns dict matching frontend ``NotificationUnreadCount``:
          {total, new_calls, callbacks, appointments, urgent}
        """
        conditions = [
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        ]

        result = await self.session.execute(
            select(
                func.count(Notification.id).label("total"),
                func.count(
                    case(
                        (Notification.type == NotificationType.NEW_CALL.value, Notification.id),
                    )
                ).label("new_calls"),
                func.count(
                    case(
                        (
                            Notification.type.in_([
                                NotificationType.CALLBACK_ITEM.value,
                                NotificationType.CALLBACK_RESOLVED.value,
                            ]),
                            Notification.id,
                        ),
                    )
                ).label("callbacks"),
                func.count(
                    case(
                        (Notification.type == NotificationType.APPOINTMENT_BOOKED.value, Notification.id),
                    )
                ).label("appointments"),
                func.count(
                    case(
                        (Notification.type == NotificationType.URGENT.value, Notification.id),
                    )
                ).label("urgent"),
            ).where(*conditions)
        )
        row = result.one()
        return {
            "total": row.total,
            "new_calls": row.new_calls,
            "callbacks": row.callbacks,
            "appointments": row.appointments,
            "urgent": row.urgent,
        }

    # ── Mutations ─────────────────────────────────────────────────────

    async def mark_as_read(self, user_id: str, notification_id: str) -> bool:
        """Mark a single notification as read.

        Only succeeds if the notification belongs to the given user (security).
        Returns False if the notification was not found or not owned by user.
        """
        result = await self.session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .values(is_read=True)
        )
        return result.rowcount > 0  # type: ignore[union-attr]

    async def mark_all_as_read(self, user_id: str) -> int:
        """Mark all unread notifications for a user as read.

        Returns the number of notifications updated.
        """
        result = await self.session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        return result.rowcount  # type: ignore[union-attr]
