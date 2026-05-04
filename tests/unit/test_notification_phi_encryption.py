from __future__ import annotations

import pytest

from src.app.config import settings
from src.app.models.notification import Notification, NotificationType


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "A" * 43)


def test_notification_payload_round_trips_through_encryption() -> None:
    notification = Notification(
        institution_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        type=NotificationType.CALLBACK_ITEM.value,
        title="Callback needed - Jane Smith",
        message="Jane Smith asked about treatment options and insurance.",
        data={
            "call_id": "33333333-3333-3333-3333-333333333333",
            "patient_name": "Jane Smith",
        },
    )

    assert notification.title == "Callback needed - Jane Smith"
    assert notification.message == "Jane Smith asked about treatment options and insurance."
    assert notification.data == {
        "call_id": "33333333-3333-3333-3333-333333333333",
        "patient_name": "Jane Smith",
    }

    assert notification.title_encrypted != notification.title
    assert notification.message_encrypted != notification.message
    assert notification.data_encrypted is not None
    assert "Jane Smith" not in notification.title_encrypted
    assert "Jane Smith" not in notification.message_encrypted
    assert "Jane Smith" not in notification.data_encrypted


def test_notification_data_can_be_cleared() -> None:
    notification = Notification(
        institution_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        type=NotificationType.NEW_CALL.value,
        title="New call",
        message="No summary available.",
        data={"call_id": "33333333-3333-3333-3333-333333333333"},
    )

    notification.data = None

    assert notification.data is None
    assert notification.data_encrypted is None
