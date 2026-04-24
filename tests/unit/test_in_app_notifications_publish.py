"""Unit tests for in-app notification SSE publish payload."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.call import CallStatus
from src.app.models.notification import NotificationType
from src.app.tasks import in_app_notifications


def test_resolve_notification_type_defaults_to_new_call() -> None:
    assert (
        in_app_notifications._resolve_notification_type(
            call_status=None, call_tags_csv=None, notification_type=None
        )
        == NotificationType.NEW_CALL.value
    )


def test_resolve_notification_type_respects_urgent_tags() -> None:
    assert (
        in_app_notifications._resolve_notification_type(
            call_status=CallStatus.EMERGENCY.value,
            call_tags_csv=None,
            notification_type=None,
        )
        == NotificationType.URGENT.value
    )


@pytest.mark.asyncio
async def test_publish_event_uses_created_count_key() -> None:
    """The SSE payload must use `created_count`, not the misleading `unread_count`."""

    svc_instance = MagicMock()
    svc_instance.create_notifications_for_call = AsyncMock(return_value=3)

    fake_call = MagicMock(id="call-1")

    session_mock = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=fake_call)
    session_mock.execute = AsyncMock(return_value=scalar_result)
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    class _SessionCM:
        async def __aenter__(self) -> Any:
            return session_mock

        async def __aexit__(self, *_: Any) -> None:
            return None

    published: list[tuple[str, str, dict[str, Any] | None]] = []

    def capture_publish(
        institution_id: str, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        published.append((institution_id, event_type, data))

    with patch.object(in_app_notifications, "get_db_session", return_value=_SessionCM()), \
        patch.object(in_app_notifications, "is_database_initialized", return_value=True), \
        patch.object(in_app_notifications, "NotificationService", return_value=svc_instance), \
        patch.object(in_app_notifications, "publish_event", side_effect=capture_publish), \
        patch.object(in_app_notifications.settings, "database_url", "postgres://test"):
        await in_app_notifications._send_in_app_notifications_async(
            call_id="call-1",
            institution_id="inst-1",
            location_id=None,
            call_status=CallStatus.APPOINTMENT_BOOKED.value,
            call_tags_csv=None,
            title=None,
            message=None,
            notification_type=None,
            data=None,
        )

    assert len(published) == 1
    inst, evt, data = published[0]
    assert inst == "inst-1"
    assert evt == "notification"
    assert data is not None
    assert "created_count" in data
    assert data["created_count"] == 3
    # Guard against regression — old field name must not come back.
    assert "unread_count" not in data


@pytest.mark.asyncio
async def test_no_publish_when_nothing_created() -> None:
    svc_instance = MagicMock()
    svc_instance.create_notifications_for_call = AsyncMock(return_value=0)
    svc_instance.create_bulk_notifications = AsyncMock(return_value=0)

    fake_call = MagicMock(id="call-2")

    session_mock = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=fake_call)
    session_mock.execute = AsyncMock(return_value=scalar_result)
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    class _SessionCM:
        async def __aenter__(self) -> Any:
            return session_mock

        async def __aexit__(self, *_: Any) -> None:
            return None

    published: list[tuple[str, str, dict[str, Any] | None]] = []

    def capture_publish(
        institution_id: str, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        published.append((institution_id, event_type, data))

    with patch.object(in_app_notifications, "get_db_session", return_value=_SessionCM()), \
        patch.object(in_app_notifications, "is_database_initialized", return_value=True), \
        patch.object(in_app_notifications, "NotificationService", return_value=svc_instance), \
        patch.object(in_app_notifications, "publish_event", side_effect=capture_publish), \
        patch.object(in_app_notifications.settings, "database_url", "postgres://test"):
        await in_app_notifications._send_in_app_notifications_async(
            call_id="call-2",
            institution_id="inst-1",
            location_id=None,
            call_status=None,
            call_tags_csv=None,
            title=None,
            message=None,
            notification_type=None,
            data=None,
        )

    assert published == []
