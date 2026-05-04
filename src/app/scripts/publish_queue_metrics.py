"""Publish Celery Redis queue depth to CloudWatch.

ECS CPU/memory scaling does not see the real worker bottleneck: backlog.
This short-lived task runs on a schedule, reads Redis LLEN for the Celery
queues, and emits a single aggregate metric plus per-queue metrics.
"""

from __future__ import annotations

import logging
import os

import boto3
from redis import Redis

logger = logging.getLogger(__name__)


def _queue_names() -> list[str]:
    raw = os.getenv("CELERY_QUEUE_DEPTH_NAMES", "notifications_default,notifications_high")
    return [name.strip() for name in raw.split(",") if name.strip()]


def main() -> int:
    redis_url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if not redis_url:
        raise SystemExit("REDIS_URL or CELERY_BROKER_URL is required")

    app_name = os.getenv("APP_NAME", "nex-health")
    app_env = os.getenv("APP_ENV", "production")
    namespace = f"{app_name}/{app_env}"
    queues = _queue_names()
    redis_client = Redis.from_url(redis_url, decode_responses=False)

    try:
        depths = {queue: int(redis_client.llen(queue)) for queue in queues}
    finally:
        redis_client.close()

    total_depth = sum(depths.values())
    metric_data = [
        {
            "MetricName": "CeleryQueueDepth",
            "Dimensions": [{"Name": "Queue", "Value": "all"}],
            "Unit": "Count",
            "Value": total_depth,
        }
    ]
    metric_data.extend(
        {
            "MetricName": "CeleryQueueDepth",
            "Dimensions": [{"Name": "Queue", "Value": queue}],
            "Unit": "Count",
            "Value": depth,
        }
        for queue, depth in depths.items()
    )

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    boto3.client("cloudwatch", region_name=region).put_metric_data(
        Namespace=namespace,
        MetricData=metric_data,
    )
    logger.info("Published Celery queue depth metrics: total=%s queues=%s", total_depth, depths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
