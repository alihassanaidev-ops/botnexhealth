"""Operator safety and tenant reachability for undeliverable work (Item 36)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.app.api.routes import dead_letter as route
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.models.user import User, UserRole
from src.app.services import dead_letter as service_module
from src.app.services.dead_letter import DeadLetterService


def _row(*, status: str = DeadLetterStatus.OPEN.value) -> DeadLetterEvent:
    now = datetime.now(timezone.utc)
    return DeadLetterEvent(
        id="11111111-1111-1111-1111-111111111111",
        source="workflow_dispatch",
        event_type="dispatch_workflow_timer",
        status=status,
        attempts=4,
        last_error="Provider unavailable",
        payload_hash="hash",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id="33333333-3333-3333-3333-333333333333",
        created_at=now,
        updated_at=now,
        raw_payload_encrypted="retained",
    )


def _user() -> User:
    return User(
        id="44444444-4444-4444-4444-444444444444",
        email="admin@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="22222222-2222-2222-2222-222222222222",
        is_active=True,
    )


def test_platform_projection_never_decrypts_tenant_free_text() -> None:
    row = _row(status=DeadLetterStatus.DISCARDED.value)
    # Deliberately not valid ciphertext. If the platform projection tries to
    # reveal it, this test fails while decrypting rather than returning None.
    row.resolution_note_encrypted = "tenant-phi"
    assert route._response(row).resolution_note is None


@pytest.mark.asyncio
async def test_resolution_lookup_takes_a_row_lock() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    await DeadLetterService(session).get_for_update("event-id")

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_two_replay_requests_enqueue_only_once(monkeypatch) -> None:
    row = _row()
    session = AsyncMock()

    class FakeService:
        def __init__(self, _session):
            pass

        async def get_for_update(self, _event_id):
            return row

        async def mark_replayed(self, event, *, user_id):
            event.status = DeadLetterStatus.REPLAYED.value
            event.resolved_by_user_id = user_id
            event.resolved_at = datetime.now(timezone.utc)

    @asynccontextmanager
    async def fake_session():
        yield session

    replay = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(route, "get_db_session", fake_session)
    monkeypatch.setattr(route, "DeadLetterService", FakeService)
    monkeypatch.setattr(route, "_replay", replay)
    monkeypatch.setattr(route, "log_audit", audit)

    first = await route._replay_dead_letter_event(
        event_id=str(row.id), current_user=_user(), include_resolution_note=False
    )
    second = await route._replay_dead_letter_event(
        event_id=str(row.id), current_user=_user(), include_resolution_note=False
    )

    assert first.status == DeadLetterStatus.REPLAYED.value
    assert second.status == DeadLetterStatus.REPLAYED.value
    replay.assert_awaited_once_with(row)
    audit.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_resolves_institution_from_location(monkeypatch) -> None:
    session = AsyncMock()
    owner_result = MagicMock()
    owner_result.scalar_one_or_none.return_value = "22222222-2222-2222-2222-222222222222"
    session.execute.return_value = owner_result
    capture = AsyncMock()

    class FakeService:
        def __init__(self, _session):
            pass

        async def capture(self, **kwargs):
            await capture(**kwargs)

    @asynccontextmanager
    async def fake_system_session(*_args, **_kwargs):
        yield session

    monkeypatch.setattr(service_module.settings, "database_url", "postgresql://configured")
    monkeypatch.setattr(service_module, "is_database_initialized", lambda: True)
    monkeypatch.setattr(service_module, "get_system_db_session", fake_system_session)
    monkeypatch.setattr(service_module, "DeadLetterService", FakeService)

    await service_module.capture_dead_letter(
        source="sms_task",
        event_type="send_sms_message",
        error="failed",
        payload={"message": "[redacted]"},
        location_id="33333333-3333-3333-3333-333333333333",
    )

    assert capture.await_args.kwargs["institution_id"] == "22222222-2222-2222-2222-222222222222"
    session.commit.assert_awaited_once()
