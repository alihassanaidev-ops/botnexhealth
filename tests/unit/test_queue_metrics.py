from __future__ import annotations

from types import SimpleNamespace

from src.app.scripts import publish_queue_metrics


def test_publish_queue_metrics_emits_total_and_per_queue(monkeypatch) -> None:
    captured: dict = {}
    close_calls = 0

    class FakeCloudWatch:
        def put_metric_data(self, **kwargs):
            captured.update(kwargs)

    def fake_client(service_name: str, *, region_name: str | None = None):
        assert service_name == "cloudwatch"
        assert region_name == "ca-central-1"
        return FakeCloudWatch()

    def fake_from_url(url: str, *, decode_responses: bool):
        assert url == "rediss://redis.example.test:6379/0"
        assert decode_responses is False

        def close() -> None:
            nonlocal close_calls
            close_calls += 1

        return SimpleNamespace(
            llen=lambda queue: {"notifications_default": 2, "notifications_high": 3}[queue],
            close=close,
        )

    monkeypatch.setenv("REDIS_URL", "rediss://redis.example.test:6379/0")
    monkeypatch.setenv("APP_NAME", "nex-health")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("AWS_REGION", "ca-central-1")
    monkeypatch.setenv("CELERY_QUEUE_DEPTH_NAMES", "notifications_default,notifications_high")
    monkeypatch.setattr(publish_queue_metrics.Redis, "from_url", fake_from_url)
    monkeypatch.setattr(publish_queue_metrics.boto3, "client", fake_client)

    assert publish_queue_metrics.main() == 0

    assert close_calls == 1
    assert captured["Namespace"] == "nex-health/staging"
    assert captured["MetricData"] == [
        {
            "MetricName": "CeleryQueueDepth",
            "Dimensions": [{"Name": "Queue", "Value": "all"}],
            "Unit": "Count",
            "Value": 5,
        },
        {
            "MetricName": "CeleryQueueDepth",
            "Dimensions": [{"Name": "Queue", "Value": "notifications_default"}],
            "Unit": "Count",
            "Value": 2,
        },
        {
            "MetricName": "CeleryQueueDepth",
            "Dimensions": [{"Name": "Queue", "Value": "notifications_high"}],
            "Unit": "Count",
            "Value": 3,
        },
    ]
