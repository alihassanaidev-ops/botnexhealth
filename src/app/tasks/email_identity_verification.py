"""Celery task: keep sending-domain verification state current.

Two jobs in one sweep.

**Newly provisioned domains** need polling until DKIM propagates, which is
usually minutes when we own the zone and can be hours or days when a clinic
publishes the records themselves.

**Already-verified domains** need re-checking, and this is the part that is easy
to skip. If a clinic's DNS records are removed later, mail does not start
bouncing — it starts failing authentication and landing in spam. Nothing errors,
nobody is paged, and the first signal is a clinic asking why patients stopped
replying. Re-checking daily turns that into an alert.
"""

from __future__ import annotations

import asyncio
import logging

from src.app.database import (
    get_superadmin_system_db_session,
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.email_sending_identity import EmailIdentityStatus
from src.app.worker import celery_app

logger = logging.getLogger(__name__)

#: Bounded so one sweep cannot spend an unbounded amount of time in AWS calls.
_SWEEP_LIMIT = 100


def _ensure_db() -> None:
    from src.app.config import settings

    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


@celery_app.task(
    name="src.app.tasks.email_identity_verification.sweep_email_identities",
    bind=True,
    max_retries=2,
    queue="maintenance",
)
def sweep_email_identities(self, *, limit: int = _SWEEP_LIMIT) -> dict:
    _ensure_db()
    try:
        return asyncio.run(_sweep_async(limit=limit))
    except Exception as exc:  # noqa: BLE001 — retried by Celery
        logger.error("email identity sweep failed: %s", exc)
        raise self.retry(exc=exc, countdown=300) from exc


async def _sweep_async(*, limit: int) -> dict:
    from src.app.models.email_sending_identity import EmailSendingIdentity
    from src.app.services.email.identity_service import EmailIdentityService

    checked = 0
    transitions: dict[str, int] = {}

    # Enumerate routing metadata globally, then refresh each identity inside its
    # owning institution. A bare celery context is intentionally not a
    # cross-tenant escape hatch under RLS.
    async with get_superadmin_system_db_session("email_identity_sweep") as session:
        identities = await EmailIdentityService(session).due_for_check(limit=limit)
        candidates = [
            (str(identity.id), str(identity.institution_id))
            for identity in identities
        ]

    for identity_id, institution_id in candidates:
        async with get_system_db_session(
            "celery",
            institution_id=institution_id,
            external_id=f"email_identity:{identity_id}",
        ) as session:
            service = EmailIdentityService(session)
            identity = await session.get(EmailSendingIdentity, identity_id)
            if identity is None:
                continue
            before = identity.status
            try:
                await service.refresh(identity)
            except Exception as exc:  # noqa: BLE001 — one bad domain must not
                # abandon the rest of the sweep.
                logger.warning(
                    "could not refresh sending identity %s: %s", identity.domain, exc
                )
                await session.rollback()
                continue

            checked += 1
            if identity.status != before:
                key = f"{before}->{identity.status}"
                transitions[key] = transitions.get(key, 0) + 1

                if identity.status == EmailIdentityStatus.REVOKED.value:
                    # Loud, because mail was flowing and is now failing
                    # authentication silently.
                    logger.error(
                        "sending domain stopped verifying: institution=%s domain=%s reason=%s",
                        identity.institution_id,
                        identity.domain,
                        identity.failure_reason,
                    )
                elif identity.status == EmailIdentityStatus.FAILED.value:
                    logger.warning(
                        "sending domain failed verification: institution=%s domain=%s reason=%s",
                        identity.institution_id,
                        identity.domain,
                        identity.failure_reason,
                    )
                elif identity.status == EmailIdentityStatus.VERIFIED.value:
                    logger.info(
                        "sending domain verified: institution=%s domain=%s",
                        identity.institution_id,
                        identity.domain,
                    )

            await session.commit()

    logger.info(
        "email identity sweep complete: checked=%d transitions=%s", checked, transitions
    )
    return {"checked": checked, "transitions": transitions}
