"""Re-syncing a location's schedule rows after its timezone is corrected.

A schedule row stores the zone its cron is read in, so the beat can claim due
rows by comparing UTC without joining back to the location. Nothing rewrites
that cache except publish/pause/resume, so a clinic whose timezone is fixed
afterwards keeps firing published campaigns on the old zone — the setting looks
corrected and the campaigns are still wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.schedule_service import WorkflowScheduleService

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _session(workflow_ids: list[str], workflows: dict[str, object]) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = MagicMock()
    result.scalars.return_value = iter(workflow_ids)
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(side_effect=lambda _model, key: workflows.get(key))
    return session


@pytest.mark.asyncio
async def test_every_campaign_at_the_location_is_resynced() -> None:
    workflows = {"wf-1": SimpleNamespace(id="wf-1"), "wf-2": SimpleNamespace(id="wf-2")}
    session = _session(["wf-1", "wf-2"], workflows)
    service = WorkflowScheduleService(session)

    with patch.object(
        WorkflowScheduleService, "sync_for_workflow", AsyncMock(return_value=1)
    ) as sync:
        resynced = await service.resync_for_location("loc-1", now=_NOW)

    assert resynced == 2
    assert {call.args[0].id for call in sync.await_args_list} == {"wf-1", "wf-2"}


@pytest.mark.asyncio
async def test_a_location_with_no_scheduled_campaigns_is_a_no_op() -> None:
    session = _session([], {})
    service = WorkflowScheduleService(session)

    with patch.object(WorkflowScheduleService, "sync_for_workflow", AsyncMock()) as sync:
        assert await service.resync_for_location("loc-1", now=_NOW) == 0

    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_deleted_workflow_is_skipped_rather_than_raising() -> None:
    """Schedule rows cascade with the workflow, but a row read a moment before
    the delete must not take the whole sweep down with it."""
    session = _session(["wf-gone"], {})
    service = WorkflowScheduleService(session)

    with patch.object(WorkflowScheduleService, "sync_for_workflow", AsyncMock()) as sync:
        assert await service.resync_for_location("loc-1", now=_NOW) == 0

    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_resync_delegates_so_a_pinned_timezone_survives() -> None:
    """A campaign that pinned a fixed zone must keep it. The choice between the
    pinned zone and the location's lives in sync_for_workflow, and re-syncing
    through it is what stops this path second-guessing that."""
    workflow = SimpleNamespace(id="wf-1")
    session = _session(["wf-1"], {"wf-1": workflow})
    service = WorkflowScheduleService(session)

    with patch.object(
        WorkflowScheduleService, "sync_for_workflow", AsyncMock(return_value=1)
    ) as sync:
        await service.resync_for_location("loc-1", now=_NOW)

    sync.assert_awaited_once_with(workflow, now=_NOW)
