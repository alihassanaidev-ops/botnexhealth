"""Unit tests for NexHealth sync-status projection service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.nexhealth_sync_status_service import (
    NexHealthSyncStatusService,
    assess_sync_status,
)


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = []
    result.all.return_value = []
    return result


def _compiled_sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _assert_nexhealth_pms_filter(stmt) -> None:
    sql = _compiled_sql(stmt)
    assert "institutions.pms_type = 'nexhealth'" in sql


@pytest.mark.asyncio
async def test_upsert_for_locations_stores_read_write_sync_state():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(None))
    session.add = MagicMock()
    location = SimpleNamespace(
        id="loc-1",
        institution_id="inst-1",
        nexhealth_location_id="nh-loc-1",
    )
    payload = {
        "data": {
            "sync_source_type": "pms",
            "sync_source_name": "Dentrix",
            "read_status": "green",
            "read_status_at": "2026-07-21T10:00:00Z",
            "write_status": "red",
            "write_status_at": "2026-07-21T10:05:00Z",
            "emr": {"id": "emr-1", "name": "dentrix"},
            "locations": [{"id": "nh-loc-1"}],
        }
    }

    updated = await NexHealthSyncStatusService(session).upsert_for_locations(
        event="sync_status_write_change",
        subdomain="clinic-sub",
        locations=[location],
        payload=payload,
    )

    assert updated == 1
    row = session.add.call_args.args[0]
    assert row.institution_id == "inst-1"
    assert row.location_id == "loc-1"
    assert row.subdomain == "clinic-sub"
    assert row.read_status == "green"
    assert row.write_status == "red"
    assert row.read_status_at == datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    assert row.last_event == "sync_status_write_change"


@pytest.mark.asyncio
async def test_upsert_for_locations_accepts_current_syncstatus_envelope():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(None))
    session.add = MagicMock()
    location = SimpleNamespace(
        id="loc-1",
        institution_id="inst-1",
        nexhealth_location_id="nh-loc-1",
    )
    payload = {
        "data": {
            "syncstatus": {
                "sync_source_type": "pms",
                "read_status": "green",
                "read_status_at": "2026-08-14T10:00:00Z",
                "write_status": "red",
                "write_status_at": "2026-08-14T10:05:00Z",
                "locations": [{"id": "nh-loc-1"}],
            }
        }
    }

    updated = await NexHealthSyncStatusService(session).upsert_for_locations(
        event="sync_status_read_change",
        subdomain="clinic-sub",
        locations=[location],
        payload=payload,
    )

    assert updated == 1
    row = session.add.call_args.args[0]
    assert row.read_status == "green"
    assert row.write_status == "red"
    assert row.read_status_at == datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)


def test_assess_sync_status_flags_unhealthy_and_stale_rows():
    row = SimpleNamespace(
        read_status="red",
        write_status="green",
        last_checked_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    assessment = assess_sync_status(row)

    assert assessment.read_healthy is False
    assert assessment.write_healthy is True
    assert assessment.stale is True


@pytest.mark.asyncio
async def test_poll_location_calls_nexhealth_sync_status_endpoint():
    session = AsyncMock()
    svc = NexHealthSyncStatusService(session)
    svc.resolve_locations_for_payload = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc.upsert_for_locations = AsyncMock(return_value=1)  # type: ignore[method-assign]
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(
        id="loc-1",
        nexhealth_subdomain="clinic-sub",
        nexhealth_location_id="nh-loc-1",
    )
    adapter = MagicMock()
    adapter._client = object()
    adapter._default_params.return_value = {
        "subdomain": "clinic-sub",
        "location_id": "nh-loc-1",
    }
    adapter.close = AsyncMock()

    with patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.api.helpers.handle_nexhealth_request",
        AsyncMock(return_value={"data": {"read_status": "green", "write_status": "green"}}),
    ) as request_mock:
        updated = await svc.poll_location(institution=institution, location=location)

    assert updated == 1
    request_mock.assert_awaited_once_with(
        adapter._client,
        "GET",
        "/sync_status",
        params={"subdomain": "clinic-sub", "location_id": "nh-loc-1"},
    )
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_locations_for_payload_only_returns_nexhealth_institutions():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(None))

    locations = await NexHealthSyncStatusService(session).resolve_locations_for_payload(
        subdomain="clinic-sub",
        payload={"data": {"locations": [{"id": "nh-loc-1"}]}},
    )

    assert locations == []
    _assert_nexhealth_pms_filter(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_sync_status_poll_target_scan_only_loads_nexhealth_institutions():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(None))

    rows = await NexHealthSyncStatusService(session)._load_subscription_locations()

    assert rows == []
    _assert_nexhealth_pms_filter(session.execute.await_args.args[0])
