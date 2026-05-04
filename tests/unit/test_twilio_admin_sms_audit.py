from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.api.routes import twilio as twilio_routes
from src.app.models.audit_log import AuditAction
from src.app.models.sms_history_log import SmsStatus


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_exc) -> None:
        return None


@pytest.mark.asyncio
async def test_admin_send_sms_audit_is_institution_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: SMS_SEND audit rows must appear in institution audit trails."""
    institution_id = "11111111-1111-4111-8111-111111111111"
    location_id = "22222222-2222-4222-8222-222222222222"
    sms_id = "33333333-3333-4333-8333-333333333333"
    session = _FakeSession()
    audit_calls: list[dict] = []

    monkeypatch.setattr(
        "src.app.database.get_db_session",
        lambda: _FakeSessionContext(session),
    )

    class FakeSmsService:
        def __init__(self, received_session: _FakeSession) -> None:
            assert received_session is session

        async def send_sms(self, **kwargs):
            assert kwargs["from_number"] == "+15550000001"
            assert kwargs["institution_location_id"] == location_id
            return SimpleNamespace(
                id=sms_id,
                institution_id=institution_id,
                status=SmsStatus.SENT.value,
                message_sid="SM_AUDIT_SCOPE_TEST",
                error_message=None,
                to_number_masked="+*******0991",
            )

    async def fake_log_audit(**kwargs) -> None:
        audit_calls.append(kwargs)

    monkeypatch.setattr("src.app.services.sms_service.SmsService", FakeSmsService)
    monkeypatch.setattr("src.app.services.audit.log_audit", fake_log_audit)

    response = await twilio_routes.send_sms.__wrapped__(
        request=SimpleNamespace(),
        body=twilio_routes.SendSmsRequest(
            from_number="+15550000001",
            to_number="+14155550991",
            body="Appointment reminder",
            institution_location_id=location_id,
        ),
        current_admin=SimpleNamespace(id="44444444-4444-4444-8444-444444444444"),
    )

    assert response.message_sid == "SM_AUDIT_SCOPE_TEST"
    assert session.commits == 1
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == AuditAction.SMS_SEND
    assert audit_calls[0]["target_resource"] == f"sms:{sms_id}"
    assert audit_calls[0]["institution_id"] == institution_id
    assert audit_calls[0]["location_id"] == location_id
