"""GoTracker patient messages must be owned by campaign workflows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.patient_communication import (
    patient_communication_requires_campaign,
)


def test_only_gotracker_requires_campaign_owned_patient_messages() -> None:
    assert patient_communication_requires_campaign("gotracker") is True
    assert patient_communication_requires_campaign(" GoTracker ") is True
    assert patient_communication_requires_campaign("nexhealth") is False
    assert patient_communication_requires_campaign("none") is False
    assert patient_communication_requires_campaign(None) is False


@pytest.mark.asyncio
async def test_gotracker_call_ended_does_not_enqueue_patient_sms(monkeypatch) -> None:
    from src.app.retell import webhooks
    from src.app.tasks import sms

    finish = AsyncMock()
    enqueue = MagicMock()
    monkeypatch.setattr(
        webhooks,
        "_resolve_institution_location_from_call",
        AsyncMock(
            return_value=(
                SimpleNamespace(id="22222222-2222-2222-2222-222222222222"),
                SimpleNamespace(
                    id="11111111-1111-1111-1111-111111111111",
                    pms_type="gotracker",
                    has_pms=True,
                ),
            )
        ),
    )
    monkeypatch.setattr(webhooks, "_finish_webhook_processing", finish)
    monkeypatch.setattr(sms, "enqueue_auto_sms", enqueue)

    result = await webhooks.process_retell_call_ended_event(
        {
            "event": "call_ended",
            "call": {
                "call_id": "call-gotracker-1",
                "agent_id": "agent-1",
                "direction": "inbound",
                "from_number": "+15550001111",
                "to_number": "+15550002222",
            },
        }
    )

    assert result == {"status": "skipped", "reason": "gotracker_campaign_only"}
    enqueue.assert_not_called()
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "COMPLETED"
    assert (
        finish.await_args.kwargs["institution_id"]
        == "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.asyncio
async def test_gotracker_post_call_patient_email_is_skipped(monkeypatch) -> None:
    from src.app.tasks import notifications

    institution_result = MagicMock()
    institution_result.scalar_one_or_none.return_value = SimpleNamespace(
        pms_type="gotracker"
    )
    session = AsyncMock()
    session.execute.return_value = institution_result
    send_notification = AsyncMock()
    monkeypatch.setattr(
        notifications.EmailNotificationService,
        "send_notification",
        send_notification,
    )

    await notifications._send_patient_appointment_email(
        session,
        institution_id="11111111-1111-1111-1111-111111111111",
        location=SimpleNamespace(id="22222222-2222-2222-2222-222222222222"),
        call=SimpleNamespace(contact=SimpleNamespace(nexhealth_patient_id="tracker-42")),
        appt={},
    )

    send_notification.assert_not_awaited()

