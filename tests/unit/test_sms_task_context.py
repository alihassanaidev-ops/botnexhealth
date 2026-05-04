from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.app.models.sms_history_log import SmsStatus
from src.app.tasks import sms as sms_task


class _LookupResult:
    def __init__(self, institution_id: str | None):
        self.institution_id = institution_id

    def scalar_one_or_none(self) -> str | None:
        return self.institution_id


class _LookupSession:
    def __init__(self, institution_id: str | None):
        self.institution_id = institution_id

    async def execute(self, *_args, **_kwargs) -> _LookupResult:
        return _LookupResult(self.institution_id)


class _SendSession:
    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_sms_task_resolves_institution_before_send_context(monkeypatch: pytest.MonkeyPatch) -> None:
    institution_id = "11111111-1111-4111-8111-111111111111"
    location_id = "22222222-2222-4222-8222-222222222222"
    context_calls: list[tuple[str, dict[str, str | None]]] = []
    send_sessions: list[object] = []

    monkeypatch.setattr(sms_task.settings, "database_url", "postgresql+asyncpg://test/test")
    monkeypatch.setattr(sms_task, "is_database_initialized", lambda: True)

    @asynccontextmanager
    async def fake_system_session(context_type: str, **kwargs: str | None):
        context_calls.append((context_type, kwargs))
        if len(context_calls) == 1:
            yield _LookupSession(institution_id)
            return
        yield _SendSession()

    class FakeSmsService:
        def __init__(self, session: object):
            send_sessions.append(session)

        async def send_sms(self, **_kwargs):
            return SimpleNamespace(
                status=SmsStatus.SENT.value,
                provider_status="sent",
                error_message=None,
                message_sid="SM123",
            )

    monkeypatch.setattr(sms_task, "get_system_db_session", fake_system_session)
    monkeypatch.setattr(sms_task, "SmsService", FakeSmsService)

    result = await sms_task._send_sms_async(
        from_number="+15550000001",
        to_number="+15550000002",
        body="Reminder",
        institution_location_id=location_id,
        patient_contact_id=None,
        call_id=None,
    )

    assert result["institution_id"] == institution_id
    assert result["status"] == SmsStatus.SENT.value
    assert len(send_sessions) == 1
    assert context_calls == [
        (
            "celery",
            {
                "location_id": location_id,
                "external_id": location_id,
            },
        ),
        (
            "celery",
            {
                "institution_id": institution_id,
                "location_id": location_id,
                "external_id": location_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_sms_task_fails_closed_when_location_cannot_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location_id = "22222222-2222-4222-8222-222222222222"
    context_calls: list[tuple[str, dict[str, str | None]]] = []

    monkeypatch.setattr(sms_task.settings, "database_url", "postgresql+asyncpg://test/test")
    monkeypatch.setattr(sms_task, "is_database_initialized", lambda: True)

    @asynccontextmanager
    async def fake_system_session(context_type: str, **kwargs: str | None):
        context_calls.append((context_type, kwargs))
        yield _LookupSession(None)

    class FailingSmsService:
        def __init__(self, _session: object):
            raise AssertionError("SMS send session should not open without an institution")

    monkeypatch.setattr(sms_task, "get_system_db_session", fake_system_session)
    monkeypatch.setattr(sms_task, "SmsService", FailingSmsService)

    with pytest.raises(ValueError, match="Institution location not found"):
        await sms_task._send_sms_async(
            from_number="+15550000001",
            to_number="+15550000002",
            body="Reminder",
            institution_location_id=location_id,
            patient_contact_id=None,
            call_id=None,
        )

    assert context_calls == [
        (
            "celery",
            {
                "location_id": location_id,
                "external_id": location_id,
            },
        )
    ]
