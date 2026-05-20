"""Apply PHI retention policy.

Designed to run as a scheduled Fargate task with admin DB credentials. The
runtime app role is intentionally RLS-bound; this job is cross-tenant and must
use ``DATABASE_ADMIN_URL`` when deployed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.services.retention_policy import RetentionPolicyService
from src.app.services.sms_privacy import safe_error_summary

logger = logging.getLogger(__name__)


async def run() -> dict[str, int]:
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit(
            "DATABASE_URL/ADMIN_URL is not set; cannot apply retention policy"
        )

    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionFactory() as session:
            service = RetentionPolicyService(session, s3_client=_get_s3_client())
            return await service.apply()
    finally:
        await engine.dispose()


def _get_s3_client():
    if not settings.aws_s3_bucket_name:
        return None

    import boto3

    return boto3.client("s3", region_name=settings.aws_region)


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        summary = asyncio.run(run())
    except Exception as exc:
        logger.error("Retention policy failed: %s", safe_error_summary(exc))
        return 1
    logger.info("Retention policy complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
