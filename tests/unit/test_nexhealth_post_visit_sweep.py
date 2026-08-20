"""NexHealth post-visit completion sweep.

NexHealth emits no checkout event, so the post-op follow-up campaign depends on
`sweep_nexhealth_completed_visits` deriving completion from the appointment's
start time plus its type duration. These pin the selection rules, the derived
timestamp, and that the shipped post-op trigger actually matches the result.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.tasks.automation_workflow import (
    NEXHEALTH_VISIT_COMPLETED,
    _sweep_nexhealth_completed_visits_async,
)

NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


def _appt(
    *,
    start_time,
    status="scheduled",
    flow_state=None,
    appointment_id="appt-1",
    location_id="loc-1",
    contact_id="contact-1",
):
    return SimpleNamespace(
        institution_id="inst-1",
        nexhealth_appointment_id=appointment_id,
        location_id=location_id,
        contact_id=contact_id,
        appointment_type_id="type-1",
        start_time=start_time,
        status=status,
        flow_state=flow_state,
        flow_changed_at=None,
        last_status_source=None,
    )


def _session(rows):
    """rows: list of (appointment, duration_minutes) as the join would return."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    result = MagicMock()
    result.unique.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


async def _run(rows):
    session = _session(rows)
    delay = MagicMock()
    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.datetime"
        ) as mock_dt,
        patch(
            "src.app.tasks.automation_workflow.trigger_appointment_state_workflows"
        ) as mock_trigger,
    ):
        mock_dt.now.return_value = NOW
        mock_trigger.delay = delay
        result = await _sweep_nexhealth_completed_visits_async()
    return result, delay, session


@pytest.mark.asyncio
async def test_finished_visit_is_marked_completed_and_triggers() -> None:
    # 60-minute visit that started 2h ago, so it ended an hour ago.
    appt = _appt(start_time=NOW - timedelta(hours=2))

    result, delay, _ = await _run([(appt, 60)])

    assert result["completed"] == 1
    assert appt.flow_state == NEXHEALTH_VISIT_COMPLETED
    delay.assert_called_once()
    kwargs = delay.call_args.kwargs
    assert kwargs["flow_state"] == NEXHEALTH_VISIT_COMPLETED
    assert kwargs["appointment_id"] == "appt-1"
    assert kwargs["institution_id"] == "inst-1"


@pytest.mark.asyncio
async def test_flow_changed_at_is_the_end_of_the_visit_not_sweep_time() -> None:
    """The post-op template waits a fixed offset from flow_changed_at, so this
    has to mean the same thing it means on GoTracker."""
    start = NOW - timedelta(hours=3)
    appt = _appt(start_time=start)

    _, delay, _ = await _run([(appt, 45)])

    expected_end = start + timedelta(minutes=45)
    assert appt.flow_changed_at == expected_end
    assert delay.call_args.kwargs["flow_changed_at"] == expected_end.isoformat()
    assert appt.flow_changed_at != NOW


@pytest.mark.asyncio
async def test_visit_still_in_progress_is_left_alone() -> None:
    """Started 10 minutes ago, 60-minute type — the patient is still in the chair."""
    appt = _appt(start_time=NOW - timedelta(minutes=10))

    result, delay, _ = await _run([(appt, 60)])

    assert result["completed"] == 0
    assert appt.flow_state is None
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_missing_duration_falls_back_to_the_configured_default() -> None:
    """A null duration_minutes must not crash or skip the row."""
    appt = _appt(start_time=NOW - timedelta(hours=2))

    result, _, _ = await _run([(appt, None)])

    # Default is 60 minutes, so a visit started 2h ago has ended.
    assert result["completed"] == 1
    assert appt.flow_changed_at == (NOW - timedelta(hours=2)) + timedelta(minutes=60)


@pytest.mark.asyncio
async def test_visit_older_than_the_lookback_is_skipped() -> None:
    """Guards against a first run firing a huge backlog of stale triggers."""
    appt = _appt(start_time=NOW - timedelta(hours=200))

    result, delay, _ = await _run([(appt, 60)])

    assert result["completed"] == 0
    assert appt.flow_state is None
    delay.assert_not_called()


def _candidate_sql() -> str:
    from src.app.tasks.automation_workflow import completed_visit_candidates_query

    query = completed_visit_candidates_query(NOW - timedelta(hours=72), NOW)
    return str(query.compile(compile_kwargs={"literal_binds": True}))


def test_sweep_is_scoped_to_nexhealth_institutions() -> None:
    """Losing this predicate would synthesize completion over GoTracker Chair Flow."""
    assert "pms_type" in _candidate_sql()
    assert "nexhealth" in _candidate_sql()


def test_cancelled_appointments_are_excluded() -> None:
    """A cancelled visit never happened; post-op must not call that patient."""
    assert "status" in _candidate_sql()
    assert "scheduled" in _candidate_sql()


def test_already_completed_rows_are_excluded() -> None:
    """Without this the sweep would re-trigger the same visit every 10 minutes."""
    sql = _candidate_sql()
    assert "flow_state" in sql
    assert NEXHEALTH_VISIT_COMPLETED in sql


def test_query_bounds_the_lookback_window() -> None:
    sql = _candidate_sql()
    assert "start_time" in sql
    # Both ends of the window are pinned, not just the upper one.
    assert sql.count("start_time") >= 3


@pytest.mark.asyncio
async def test_marks_are_committed_before_triggers_fire() -> None:
    """A crash between the two must re-mark, never double-enrol."""
    appt = _appt(start_time=NOW - timedelta(hours=2))
    session = _session([(appt, 60)])
    order: list[str] = []
    session.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch("src.app.tasks.automation_workflow.datetime") as mock_dt,
        patch(
            "src.app.tasks.automation_workflow.trigger_appointment_state_workflows"
        ) as mock_trigger,
    ):
        mock_dt.now.return_value = NOW
        mock_trigger.delay = MagicMock(side_effect=lambda **_: order.append("trigger"))
        await _sweep_nexhealth_completed_visits_async()

    assert order == ["commit", "trigger"]


@pytest.mark.asyncio
async def test_several_visits_each_trigger_once() -> None:
    rows = [
        (_appt(start_time=NOW - timedelta(hours=2), appointment_id="a-1"), 60),
        (_appt(start_time=NOW - timedelta(hours=3), appointment_id="a-2"), 30),
        # still in progress, must not fire
        (_appt(start_time=NOW - timedelta(minutes=5), appointment_id="a-3"), 60),
    ]

    result, delay, _ = await _run(rows)

    assert result["completed"] == 2
    fired = {call.kwargs["appointment_id"] for call in delay.call_args_list}
    assert fired == {"a-1", "a-2"}


def test_synthesized_completion_matches_the_shipped_post_op_trigger() -> None:
    """The whole point: one template definition enrols on either PMS."""
    from src.app.services.automation.appointment_trigger_service import (
        workflow_matches_appointment_state,
    )
    from src.app.services.automation.campaign_templates import (
        _POST_OP_FOLLOWUP_AFTER_CONFIRMATION,
    )

    workflow = SimpleNamespace(
        definition=_POST_OP_FOLLOWUP_AFTER_CONFIRMATION,
        current_version_id="ver-1",
    )

    assert workflow_matches_appointment_state(
        workflow,
        status_id=None,
        confirmed=None,
        preconfirmed=None,
        flow_state=NEXHEALTH_VISIT_COMPLETED,
    )


def test_a_non_completed_flow_state_does_not_match_post_op() -> None:
    from src.app.services.automation.appointment_trigger_service import (
        workflow_matches_appointment_state,
    )
    from src.app.services.automation.campaign_templates import (
        _POST_OP_FOLLOWUP_AFTER_CONFIRMATION,
    )

    workflow = SimpleNamespace(
        definition=_POST_OP_FOLLOWUP_AFTER_CONFIRMATION,
        current_version_id="ver-1",
    )

    assert not workflow_matches_appointment_state(
        workflow,
        status_id=None,
        confirmed=None,
        preconfirmed=None,
        flow_state="InChair",
    )
