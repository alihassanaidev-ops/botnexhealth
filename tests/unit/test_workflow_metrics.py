from __future__ import annotations

import contextlib

import pytest

from src.app.scripts import publish_workflow_metrics


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    """Returns the preset scalar counts in the order the queries run."""

    def __init__(self, values: list[int]) -> None:
        self._values = iter(values)
        self.execute_calls = 0

    async def execute(self, _stmt):  # noqa: ANN001
        self.execute_calls += 1
        return _FakeResult(next(self._values))


@pytest.mark.asyncio
async def test_publish_workflow_metrics_emits_all_signals(monkeypatch) -> None:
    captured: dict = {}

    class FakeCloudWatch:
        def put_metric_data(self, **kwargs):
            captured.update(kwargs)

    def fake_client(service_name: str, *, region_name: str | None = None):
        assert service_name == "cloudwatch"
        assert region_name == "ca-central-1"
        return FakeCloudWatch()

    # due_timer_backlog, stale_timers, active_runs, failed_runs, failed_steps
    fake_session = _FakeSession([7, 2, 5, 3, 4])

    @contextlib.asynccontextmanager
    async def fake_get_system_db_session(context_type: str, **kwargs):
        assert context_type == "celery"
        yield fake_session

    monkeypatch.setenv("APP_NAME", "nex-health")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("AWS_REGION", "ca-central-1")
    # Keep _ensure_db a no-op — the DB session is fully mocked below.
    monkeypatch.setattr(publish_workflow_metrics, "is_database_initialized", lambda: True)
    monkeypatch.setattr(
        publish_workflow_metrics, "get_system_db_session", fake_get_system_db_session
    )
    monkeypatch.setattr(publish_workflow_metrics.boto3, "client", fake_client)

    counts = await publish_workflow_metrics.publish_workflow_metrics()

    assert fake_session.execute_calls == 5
    assert counts == {
        "due_timer_backlog": 7,
        "stale_timers": 2,
        "active_runs": 5,
        "failed_runs": 3,
        "failed_steps": 4,
    }
    assert captured["Namespace"] == "nex-health/staging"
    assert captured["MetricData"] == [
        {"MetricName": "WorkflowDueTimerBacklog", "Unit": "Count", "Value": 7},
        {"MetricName": "WorkflowStaleTimers", "Unit": "Count", "Value": 2},
        {"MetricName": "WorkflowActiveRuns", "Unit": "Count", "Value": 5},
        {"MetricName": "WorkflowFailedRuns", "Unit": "Count", "Value": 3},
        {"MetricName": "WorkflowFailedSteps", "Unit": "Count", "Value": 4},
    ]
