"""Learning a NexHealth clinic's timezone, and making the fix reach campaigns.

`InstitutionLocation.timezone` defaults to "UTC" and nothing populated it for
NexHealth: unlike GoTracker, whose appointment webhook carries the zone on every
delivery, NexHealth's appointment payload carries none — only ``start_time``,
whose offset describes one instant and cannot name a zone. The practice's own
location record does carry one, so the value has to be pulled.

The second half matters as much as the first: a published campaign's schedule
row caches the zone its cron fires in, so correcting the location without
re-syncing leaves every already-published campaign on UTC while the setting
reads as fixed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.nexhealth_sync_status_service import (
    NexHealthSyncStatusService,
)

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = iter([])
    session.execute = AsyncMock(return_value=result)
    return session


def _location(
    tz: str = "UTC",
    nexhealth_location_id: str | None = "nh-77",
    timezone_checked_at: datetime | None = None,
):
    return SimpleNamespace(
        id="loc-1",
        institution_id="inst-1",
        timezone=tz,
        nexhealth_subdomain="clinic-sub",
        nexhealth_location_id=nexhealth_location_id,
        timezone_checked_at=timezone_checked_at,
    )


def _adapter(timezone_name: str | None):
    adapter = AsyncMock()
    adapter.get_location = AsyncMock(
        return_value=SimpleNamespace(timezone=timezone_name)
    )
    adapter.close = AsyncMock()
    return adapter


def _patch_adapter(adapter):
    return patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    )


@pytest.mark.asyncio
async def test_an_unset_location_adopts_the_zone_nexhealth_reports() -> None:
    session = _session()
    location = _location()
    adapter = _adapter("America/Chicago")

    with _patch_adapter(adapter):
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned == "America/Chicago"
    assert location.timezone == "America/Chicago"
    adapter.get_location.assert_awaited_once_with("nh-77")
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_configured_zone_is_compared_but_never_overwritten() -> None:
    """An administrator's choice stands. We cannot tell one working around a bad
    PMS record from one who made a typo, so the disagreement is reported rather
    than silently resolved in either direction."""
    session = _session()
    location = _location("America/Vancouver")

    with _patch_adapter(_adapter("America/Chicago")):
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned is None
    assert location.timezone == "America/Vancouver"


@pytest.mark.asyncio
async def test_drift_between_the_configured_and_reported_zone_is_logged(caplog) -> None:
    """The whole point of still asking: a typo made once at onboarding would
    otherwise mistime every send for the life of the clinic, silently."""
    session = _session()
    location = _location("America/Vancouver")

    with _patch_adapter(_adapter("America/Chicago")), caplog.at_level("WARNING"):
        await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert "timezone drift" in caplog.text
    assert "configured=America/Vancouver" in caplog.text
    assert "pms_reports=America/Chicago" in caplog.text


@pytest.mark.asyncio
async def test_agreement_is_not_reported_as_drift(caplog) -> None:
    session = _session()
    location = _location("America/Chicago")

    with _patch_adapter(_adapter("America/Chicago")), caplog.at_level("WARNING"):
        await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert "timezone drift" not in caplog.text


@pytest.mark.asyncio
async def test_a_location_checked_today_is_not_asked_again() -> None:
    """The sweep runs every 15 minutes and background PMS traffic shares a
    60/minute allowance, so this pull cannot ride it."""
    session = _session()
    location = _location(
        "America/Vancouver",
        timezone_checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    with _patch_adapter(_adapter("America/Chicago")) as create:
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned is None
    create.assert_not_called()


@pytest.mark.asyncio
async def test_a_location_checked_yesterday_is_asked_again() -> None:
    session = _session()
    location = _location(
        timezone_checked_at=datetime.now(timezone.utc) - timedelta(hours=25)
    )

    with _patch_adapter(_adapter("America/Chicago")):
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned == "America/Chicago"


@pytest.mark.asyncio
async def test_a_naive_stored_timestamp_is_read_as_utc() -> None:
    """Rows written before the column was timezone-aware must not compare as if
    they were decades old and trigger a request on every sweep."""
    session = _session()
    location = _location(
        "America/Vancouver",
        timezone_checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with _patch_adapter(_adapter("America/Chicago")) as create:
        await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    create.assert_not_called()


@pytest.mark.asyncio
async def test_an_unusable_answer_still_stamps_the_check() -> None:
    """A practice whose record carries no zone would otherwise be retried four
    times an hour, for ever, against a shared rate allowance."""
    session = _session()
    location = _location()
    adapter = _adapter(None)
    adapter.get_location = AsyncMock(return_value=None)

    with _patch_adapter(adapter):
        await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert location.timezone_checked_at is not None


@pytest.mark.asyncio
async def test_a_location_with_no_nexhealth_id_is_skipped() -> None:
    session = _session()
    location = _location(nexhealth_location_id=None)

    with _patch_adapter(_adapter("America/Chicago")) as create:
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned is None
    create.assert_not_called()


@pytest.mark.asyncio
async def test_an_unrecognised_zone_is_ignored_rather_than_stored() -> None:
    """Storing a name we cannot resolve reinstates the original bug with extra
    steps: it reads back as UTC on every lookup, and blocks the retry."""
    session = _session()
    location = _location()

    with _patch_adapter(_adapter("Mars/Olympus")):
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned is None
    assert location.timezone == "UTC"


@pytest.mark.asyncio
async def test_an_unreachable_location_record_leaves_the_default_in_place() -> None:
    session = _session()
    location = _location()
    adapter = _adapter(None)
    adapter.get_location = AsyncMock(return_value=None)

    with _patch_adapter(adapter):
        learned = await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    assert learned is None
    assert location.timezone == "UTC"
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_learning_a_zone_resyncs_the_locations_published_campaigns() -> None:
    """The point of the whole change: a campaign published while the clinic
    said UTC keeps firing on UTC until its schedule row is rewritten."""
    session = _session()
    location = _location()

    resync = AsyncMock(return_value=2)
    with (
        _patch_adapter(_adapter("America/Chicago")),
        patch(
            "src.app.services.automation.schedule_service."
            "WorkflowScheduleService.resync_for_location",
            resync,
        ),
    ):
        await NexHealthSyncStatusService(session).learn_timezone(
            institution=SimpleNamespace(id="inst-1"), location=location
        )

    resync.assert_awaited_once_with("loc-1")


@pytest.mark.asyncio
async def test_a_timezone_failure_does_not_mark_the_integration_unhealthy() -> None:
    """A clinic whose location record we cannot read is not a broken sync, and
    a sync-status poll that fails must not stop the zone being learned."""
    service = NexHealthSyncStatusService(_session())
    row = SimpleNamespace(
        institution=SimpleNamespace(id="inst-1"),
        location=_location(),
        subscription=SimpleNamespace(
            status="active",
            last_health_check_at=None,
            updated_at=None,
            error_metadata=None,
        ),
    )

    service._load_subscription_locations = AsyncMock(return_value=[row])  # noqa: SLF001
    service.learn_timezone = AsyncMock(side_effect=RuntimeError("nexhealth down"))
    service.poll_location = AsyncMock(return_value=1)

    summary = await service.poll_all_configured_locations()

    assert summary.locations_checked == 1
    assert summary.failed_locations == 0
    assert summary.timezones_learned == 0
    assert row.subscription.error_metadata is None


def test_the_location_mapper_reads_the_key_nexhealth_actually_sends() -> None:
    """NexHealth calls this field ``tz`` and returns a real IANA zone in it.
    There is no ``timezone`` key on the location record, so reading that name
    produced None for every location — the value looked absent, not misread,
    which is why nothing downstream ever reported a problem.
    """
    from src.app.pms.nexhealth.mappers import to_location

    mapped = to_location(
        {"id": 340582, "name": "Relaxation Dental", "tz": "America/Los_Angeles"}
    )
    assert mapped.timezone == "America/Los_Angeles"


def test_the_mapper_still_accepts_a_timezone_key() -> None:
    """Kept as a fallback: the v3 location payloads are not all verified."""
    from src.app.pms.nexhealth.mappers import to_location

    mapped = to_location({"id": 1, "name": "x", "timezone": "America/Toronto"})
    assert mapped.timezone == "America/Toronto"
