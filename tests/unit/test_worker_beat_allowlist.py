"""CELERY_BEAT_TASKS limits which periodic jobs beat starts."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app import worker


def _schedule(value):
    with patch.object(worker.settings, "celery_beat_tasks", value):
        return worker._beat_schedule()


def test_unset_runs_every_job():
    assert _schedule(None) == worker._BEAT_SCHEDULE
    assert _schedule("") == worker._BEAT_SCHEDULE


def test_allowlist_runs_only_the_named_jobs():
    jobs = _schedule(" tick-workflow-schedules, poll-workflow-timers ")

    assert set(jobs) == {"tick-workflow-schedules", "poll-workflow-timers"}
    assert "reconcile-nexhealth-patients" not in jobs


def test_production_campaign_jobs_all_exist():
    names = (
        "tick-workflow-schedules,poll-workflow-timers,"
        "recover-stale-workflow-timers,poll-retell-voice-outcomes"
    )
    assert len(_schedule(names)) == 4


def test_unknown_job_name_fails_startup():
    with pytest.raises(ValueError, match="tick-workflow-schedule"):
        _schedule("tick-workflow-schedule")
