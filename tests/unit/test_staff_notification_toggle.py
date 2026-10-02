"""The institution switch mutes staff alerts without touching patient mail.

The switch exists so a practice can work from the dashboard instead of the
inbox. A patient confirmation is a different kind of thing — a promise made to
the patient — so it must survive the clinic muting its own alerts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.app.services.email_notification_service import EmailNotificationService

_PAYLOAD = {"call_status_label": "Booked", "dashboard_link": "https://example.test/c/1"}


async def _send(*, enabled: bool, patient_facing: bool) -> int:
    """Send once and report how many HTTP requests actually left."""
    svc = EmailNotificationService()
    with patch(
        "src.app.services.email_notification_service._staff_notifications_enabled",
        AsyncMock(return_value=enabled),
    ), patch(
        "src.app.services.email_notification_service.settings.resend_api_key", "test-key"
    ), patch(
        "src.app.services.email_notification_service.settings.resend_from_email",
        "alerts@example.test",
    ), patch(
        "httpx.AsyncClient.post", AsyncMock()
    ) as post:
        post.return_value.status_code = 200
        post.return_value.json = lambda: {"id": "msg-1"}
        try:
            await svc.send_notification(
                recipients=["staff@example.test"],
                payload=_PAYLOAD,
                idempotency_key="key-1",
                template_type="call_summary",
                institution_id="inst-1",
                patient_facing=patient_facing,
            )
        except Exception:
            # Delivery details are not what these tests are about; whether the
            # request was attempted at all is.
            pass
        return post.await_count


@pytest.mark.asyncio
async def test_staff_alert_is_suppressed_when_the_switch_is_off() -> None:
    assert await _send(enabled=False, patient_facing=False) == 0


@pytest.mark.asyncio
async def test_staff_alert_is_sent_when_the_switch_is_on() -> None:
    assert await _send(enabled=True, patient_facing=False) > 0


@pytest.mark.asyncio
async def test_patient_email_is_never_gated_by_the_staff_switch() -> None:
    assert await _send(enabled=False, patient_facing=True) > 0
