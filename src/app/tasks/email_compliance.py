"""Celery task: suppress email for a recipient (Plan 05).

Writes a REVOKED EMAIL consent record so the compliance gate blocks future email
to that address (a revoked record beats implied transactional consent). Invoked by
the unsubscribe route and the Resend bounce/complaint webhook — both verify first,
then enqueue this so the write happens under the ``celery`` RLS context (which is
authorized for consent_records) and the public route stays fast.
"""

from __future__ import annotations

import asyncio
import logging

from src.app.database import get_system_db_session, init_database, is_database_initialized
from src.app.models.sms_consent import ConsentSource, ConsentStatus
from src.app.services.sms_compliance import SmsComplianceService
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


def _ensure_db() -> None:
    from src.app.config import settings
    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


@celery_app.task(
    name="src.app.tasks.email_compliance.suppress_email_consent",
    bind=True,
    max_retries=3,
    queue="webhooks",
)
def suppress_email_consent(
    self,
    *,
    institution_id: str,
    email_hash: str,
    reason: str,
) -> dict:
    """Record a revoked EMAIL consent for an email identity. Idempotent-ish: a
    duplicate revoke simply appends another revoked row; the gate reads the latest."""
    _ensure_db()
    try:
        return asyncio.run(
            _suppress_async(institution_id=institution_id, email_hash=email_hash, reason=reason)
        )
    except Exception as exc:
        logger.exception("suppress_email_consent failed: institution=%s: %s", institution_id, exc)
        raise self.retry(exc=exc, countdown=30)


async def _suppress_async(*, institution_id: str, email_hash: str, reason: str) -> dict:
    async with get_system_db_session("celery", institution_id=institution_id) as session:
        await SmsComplianceService(session).record_email_consent_identity(
            institution_id=institution_id,
            email_hash=email_hash,
            status=ConsentStatus.REVOKED,
            source=ConsentSource.SYSTEM,
            reason=reason,
        )
        await session.commit()
    logger.info("email suppressed: institution=%s reason=%s", institution_id, reason)
    return {"institution_id": institution_id, "suppressed": True}
