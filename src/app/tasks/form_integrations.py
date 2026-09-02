"""Celery: keep connected lead forms actually delivering.

Two jobs, both about the same failure: a form integration does not break loudly.
It stops producing leads, and a practice notices weeks later when somebody asks
why nobody has enquired.

**The connection sweep** watches for authorisations that are about to die.
Meta's grant expires roughly every sixty days, and until it is renewed every
lead on that Page is lost. Waiting for the next manual sync to discover it means
discovering it after the fact.

**The reconciliation sweep** re-reads recent submissions straight from the
provider and lands anything the webhook never delivered. Webhooks are the normal
path and are fine; the case this exists for is the deploy, the outage or the
expired certificate during which the provider gave up retrying. Without it those
leads are gone permanently, and nothing anywhere records that they existed.

Both are idempotent. Reconciliation relies on the same
``(institution, form, external_submission_id)`` claim the webhook does, so a
submission already landed is skipped rather than duplicated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.app.database import (
    get_superadmin_system_db_session,
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.form_integration import (
    FormConnectionStatus,
    FormDefinition,
    FormProvider,
    FormProviderConnection,
)
from src.app.worker import celery_app

logger = logging.getLogger(__name__)

#: How long before expiry a connection is called out. Long enough that somebody
#: has to act during a working week rather than the morning leads stop.
EXPIRY_WARNING_DAYS = 7

#: How far back a reconciliation pass looks when a form has never received a
#: submission. Bounded so a first run on an established form does not import
#: months of history nobody expects.
RECONCILE_LOOKBACK_HOURS = 24

#: Overlap on top of the last submission we hold, because provider clocks and
#: ours differ and a lead landing exactly on the boundary would be skipped.
RECONCILE_OVERLAP_MINUTES = 10

_SWEEP_LIMIT = 200


def _ensure_db() -> None:
    from src.app.config import settings

    if not is_database_initialized() and settings.database_url:
        init_database(settings.database_url, use_null_pool=True)


# ---------------------------------------------------------------------------
# Connection health
# ---------------------------------------------------------------------------
@celery_app.task(
    name="src.app.tasks.form_integrations.sweep_form_connections",
    bind=True,
    max_retries=2,
    queue="maintenance",
)
def sweep_form_connections(self, *, limit: int = _SWEEP_LIMIT) -> dict:
    _ensure_db()
    try:
        return asyncio.run(_sweep_connections_async(limit=limit))
    except Exception as exc:  # noqa: BLE001 — retried by Celery
        logger.error("form connection sweep failed: %s", exc)
        raise self.retry(exc=exc, countdown=300) from exc


async def _sweep_connections_async(*, limit: int) -> dict:
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(days=EXPIRY_WARNING_DAYS)

    # Enumerate globally, then act inside each owning institution. A bare celery
    # context is deliberately not a cross-tenant escape hatch under RLS.
    async with get_superadmin_system_db_session("form_connection_sweep") as session:
        rows = (
            (
                await session.execute(
                    select(FormProviderConnection)
                    .where(
                        FormProviderConnection.disconnected_at.is_(None),
                        FormProviderConnection.status
                        == FormConnectionStatus.ACTIVE.value,
                        FormProviderConnection.token_expires_at.is_not(None),
                        FormProviderConnection.token_expires_at <= deadline,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        candidates = [
            (str(row.id), str(row.institution_id), row.token_expires_at)
            for row in rows
        ]

    expiring = 0
    expired = 0
    for connection_id, institution_id, expires_at in candidates:
        async with get_system_db_session(
            "celery",
            institution_id=institution_id,
            external_id=f"form_connection:{connection_id}",
        ) as session:
            connection = await session.get(FormProviderConnection, connection_id)
            if connection is None:
                continue

            if expires_at is not None and expires_at <= now:
                # Already dead. Every lead on this account is being lost right
                # now, which is why this is an error and not a warning.
                connection.status = FormConnectionStatus.NEEDS_REAUTH.value
                connection.last_error = (
                    "This connection expired. Reconnect the account — no new "
                    "leads are arriving from it."
                )
                expired += 1
                logger.error(
                    "form connection expired: institution=%s provider=%s account=%s",
                    institution_id,
                    connection.provider,
                    connection.account_ref,
                )
            else:
                # Still working. Recorded on the row rather than flipping the
                # status, so the settings screen can warn without claiming the
                # integration is already broken.
                connection.last_error = (
                    f"This connection expires on "
                    f"{expires_at:%d %b %Y}. Reconnect the account before then "
                    "to avoid losing leads."
                )
                expiring += 1
                logger.warning(
                    "form connection expiring: institution=%s provider=%s account=%s at=%s",
                    institution_id,
                    connection.provider,
                    connection.account_ref,
                    expires_at,
                )
            await session.commit()

    logger.info(
        "form connection sweep complete: expiring=%d expired=%d", expiring, expired
    )
    return {"expiring": expiring, "expired": expired, "checked": len(candidates)}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
@celery_app.task(
    name="src.app.tasks.form_integrations.reconcile_form_submissions",
    bind=True,
    max_retries=2,
    queue="maintenance",
)
def reconcile_form_submissions(self, *, limit: int = _SWEEP_LIMIT) -> dict:
    _ensure_db()
    try:
        return asyncio.run(_reconcile_async(limit=limit))
    except Exception as exc:  # noqa: BLE001 — retried by Celery
        logger.error("form reconciliation failed: %s", exc)
        raise self.retry(exc=exc, countdown=600) from exc


async def _reconcile_async(*, limit: int) -> dict:
    async with get_superadmin_system_db_session("form_reconcile") as session:
        rows = (
            (
                await session.execute(
                    select(FormDefinition)
                    .where(
                        FormDefinition.is_enabled.is_(True),
                        FormDefinition.archived_at.is_(None),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        candidates = [(str(row.id), str(row.institution_id)) for row in rows]

    recovered = 0
    checked = 0
    for form_id, institution_id in candidates:
        try:
            recovered += await _reconcile_one(
                form_id=form_id, institution_id=institution_id
            )
            checked += 1
        except Exception:  # noqa: BLE001 — one bad form must not end the sweep
            logger.exception("form reconciliation failed for form=%s", form_id)

    logger.info(
        "form reconciliation complete: forms=%d recovered=%d", checked, recovered
    )
    return {"forms": checked, "recovered": recovered}


async def _reconcile_one(*, form_id: str, institution_id: str) -> int:
    """Re-read one form's recent submissions and land anything missing."""
    from src.app.services.automation.form_trigger_service import (
        FormTriggerService,
        enqueue_form_workflow_dispatches,
    )
    from src.app.services.forms import connection_service
    from src.app.services.forms.providers import meta as meta_provider
    from src.app.services.forms.providers import typeform as typeform_provider
    from src.app.services.forms.providers.base import FormProviderError
    from src.app.services.forms.submission_service import (
        SubmissionRejected,
        land_submission,
        submission_trigger_context,
    )

    dispatches = []
    recovered = 0

    async with get_system_db_session(
        "celery", institution_id=institution_id, external_id=f"form:{form_id}"
    ) as session:
        form = await session.get(FormDefinition, form_id)
        if form is None:
            return 0
        connection = await session.get(FormProviderConnection, form.connection_id)
        if connection is None or connection.disconnected_at is not None:
            return 0

        since = _since_for(form.last_submission_at)
        try:
            account = connection_service.account_from_connection(connection)
            if form.provider == FormProvider.TYPEFORM.value:
                submissions = await typeform_provider.TypeformClient().list_responses(
                    account, form.external_form_id, since=since
                )
            else:
                submissions = await meta_provider.MetaFormClient().list_leads(
                    account, form.external_form_id, since=since
                )
        except FormProviderError as error:
            connection_service.mark_connection_failure(connection, error)
            await session.commit()
            logger.warning(
                "form reconciliation could not read form=%s: %s", form_id, error
            )
            return 0

        for submission in submissions:
            try:
                landed = await land_submission(
                    session, form=form, submission=submission, raw_body=None
                )
            except SubmissionRejected:
                # Already reported through the webhook path when it arrived
                # there; re-recording it on every sweep would inflate the count.
                continue
            if landed is None:
                # The overwhelmingly common case: the webhook already had it.
                continue

            recovered += 1
            context = submission_trigger_context(form=form, landed=landed)
            dispatches.extend(
                await FormTriggerService(session).prepare_dispatches(
                    institution_id=institution_id,
                    location_id=str(form.location_id) if form.location_id else None,
                    contact_id=str(landed.contact.id),
                    submission_id=str(landed.submission.id),
                    context=context,
                )
            )

        await session.commit()

    if dispatches:
        # After the commit, so a worker cannot read a row that is still
        # uncommitted — or one that rolled back.
        enqueue_form_workflow_dispatches(dispatches)
    if recovered:
        logger.warning(
            "form reconciliation recovered %d missed submission(s) form=%s",
            recovered,
            form_id,
        )
    return recovered


def _since_for(last_submission_at: datetime | None) -> datetime:
    """Where to resume from, with overlap so a boundary lead is not skipped."""
    now = datetime.now(timezone.utc)
    if last_submission_at is None:
        return now - timedelta(hours=RECONCILE_LOOKBACK_HOURS)
    if last_submission_at.tzinfo is None:
        last_submission_at = last_submission_at.replace(tzinfo=timezone.utc)
    return min(
        last_submission_at - timedelta(minutes=RECONCILE_OVERLAP_MINUTES),
        now,
    )
