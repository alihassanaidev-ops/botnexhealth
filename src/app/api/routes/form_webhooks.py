"""Where Meta and Typeform deliver submitted forms.

Both are public endpoints that create contacts, so both are treated as hostile
input until a signature proves otherwise. The shared shape:

1. Read the raw bytes. Signatures are over exactly what arrived, so nothing may
   re-serialise the body first.
2. Resolve which clinic it belongs to *before* opening a tenant session, under
   a lookup context that can see only the one row the request names.
3. Verify the signature. Unverifiable is refused: a delivery we cannot
   attribute is indistinguishable from a forged one, and this endpoint writes
   people into a clinic's records.
4. Land the submission idempotently, then enroll workflows after the
   transaction has committed.

The two differ in what identifies the clinic. Typeform posts to a per-form URL
we chose at registration, so the form row id is in the path and the signing
secret is that form's own. Meta posts every app's leads to one URL and names the
Page only inside the body, so the Page id resolves the connection and the
signature is checked with the platform app secret.

Answers are never logged and never echoed. The reply says only that the delivery
was accepted — a webhook that reported "already known" or "no contact method"
would tell whoever can reach it something about the clinic's records.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select

from src.app.api.rate_limit import limiter
from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.form_integration import (
    FormDefinition,
    FormProvider,
    FormProviderConnection,
)
from src.app.services.automation.form_trigger_service import (
    FormTriggerService,
    FormWorkflowDispatch,
    enqueue_form_workflow_dispatches,
)
from src.app.services.forms import connection_service
from src.app.services.forms.providers import meta as meta_provider
from src.app.services.forms.providers import typeform as typeform_provider
from src.app.services.forms.providers.base import FormProviderError
from src.app.services.forms.submission_service import (
    SubmissionRejected,
    land_submission,
    record_unprocessed_submission,
    submission_trigger_context,
)
from src.app.models.form_integration import FormSubmissionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forms/webhooks", tags=["Form Webhooks"])

#: Full tenant context for a verified delivery.
WEBHOOK_CONTEXT = "form_webhook"
#: Pre-tenant. Sees exactly the connection or form row the request names.
LOOKUP_CONTEXT = "form_webhook_lookup"

#: A form provider posts from a small pool of addresses and can burst when a
#: campaign lands, so this is a runaway backstop rather than the real control.
#: The signature is what authorises.
RATE = "600/minute"

MAX_BODY_BYTES = 512 * 1024

_ACCEPTED = {"status": "received"}


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
@router.get("/meta", response_class=PlainTextResponse)
async def meta_verify(
    request: Request,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> PlainTextResponse:
    """Meta's subscription handshake.

    Meta will not deliver a single lead until this GET echoes the challenge, so
    a deployment with no verify token configured cannot receive leads at all —
    which is why the failure is a plain 403 rather than something quieter.
    """
    expected = settings.meta_webhook_verify_token
    if not expected or hub_mode != "subscribe" or hub_verify_token != expected:
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(hub_challenge or "")


@router.post("/meta")
@limiter.limit(RATE)
async def meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> JSONResponse:
    """One or more leadgen notifications, for any Page connected to this app."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _json({"error": "payload_too_large"}, 413)

    if not meta_provider.verify_webhook_signature(raw, x_hub_signature_256):
        logger.info("meta form webhook rejected: signature mismatch")
        return _json({"error": "unauthorised"}, 401)

    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return _json({"error": "invalid_payload"}, 400)

    dispatches: list[FormWorkflowDispatch] = []
    for page_id, leadgen_id, form_external_id in _meta_leads(payload):
        try:
            dispatches.extend(
                await _handle_meta_lead(
                    page_id=page_id,
                    leadgen_id=leadgen_id,
                    form_external_id=form_external_id,
                )
            )
        except Exception:  # noqa: BLE001 — one bad lead must not drop the batch
            # Meta batches several leads into one delivery and retries the
            # whole thing on a non-2xx. Failing the batch would redeliver the
            # ones that already landed, so each is isolated.
            logger.exception("meta form webhook: lead %s failed", leadgen_id)

    _enqueue(dispatches)
    # Always 200 once the signature checked out: a non-2xx makes Meta retry,
    # and nothing above is retryable in a way a redelivery would fix.
    return _json(_ACCEPTED, 200)


def _meta_leads(payload: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """``(page_id, leadgen_id, form_id)`` for every leadgen change in the body."""
    leads: list[tuple[str, str, str | None]] = []
    if str(payload.get("object") or "") != "page":
        return leads
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get("id") or "").strip()
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if str(change.get("field") or "") != "leadgen":
                continue
            value = change.get("value") or {}
            leadgen_id = str(value.get("leadgen_id") or "").strip()
            if not page_id or not leadgen_id:
                continue
            leads.append(
                (page_id, leadgen_id, str(value.get("form_id") or "") or None)
            )
    return leads


async def _handle_meta_lead(
    *, page_id: str, leadgen_id: str, form_external_id: str | None
) -> list[FormWorkflowDispatch]:
    # The Page id is the only handle on the tenant, and it is read under a
    # policy that can match nothing else.
    async with get_system_db_session(
        LOOKUP_CONTEXT, external_id=page_id
    ) as session:
        connection = (
            (
                await session.execute(
                    select(FormProviderConnection).where(
                        FormProviderConnection.provider == FormProvider.META.value,
                        FormProviderConnection.account_ref == page_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        institution_id = str(connection.institution_id) if connection else None
        connection_id = str(connection.id) if connection else None

    if not institution_id or not connection_id:
        # A Page we are not connected to. Nothing to do, and deliberately no
        # distinct reply — the caller already got 200 for the batch.
        logger.info("meta form webhook: no connection for page=%s", page_id)
        return []

    async with get_system_db_session(
        WEBHOOK_CONTEXT,
        institution_id=institution_id,
        external_id=connection_id,
    ) as session:
        connection_row = await session.get(FormProviderConnection, connection_id)
        if connection_row is None:
            return []

        form, disabled_form = await _resolve_meta_form(
            session,
            institution_id=institution_id,
            connection_id=connection_id,
            form_external_id=form_external_id,
        )
        if form is None:
            # A known form that is simply switched off is recorded against that
            # form, so the practice can see leads are arriving for something
            # they have not turned on. Anything else has no form to attach to,
            # so it lands on the connection where the settings screen shows it.
            if disabled_form is not None:
                await record_unprocessed_submission(
                    session,
                    form=disabled_form,
                    external_submission_id=leadgen_id,
                    status=FormSubmissionStatus.DROPPED.value,
                    reason=(
                        "A lead arrived for this form while it was switched off. "
                        "Switch it on to start bringing these in."
                    ),
                )
            else:
                connection_row.last_error = (
                    "A lead arrived that named no form, and this Page has more "
                    "than one switched-on form, so it could not be matched. "
                    "Leave one form switched on, or ask Meta support why the "
                    "delivery omits the form id."
                )[:500]
            return []

        # The webhook body carries an id, not the answers. They have to be
        # fetched back with the Page token.
        try:
            account = connection_service.account_from_connection(connection_row)
            submission = await meta_provider.MetaFormClient().fetch_lead(
                account, leadgen_id
            )
        except FormProviderError as error:
            connection_service.mark_connection_failure(connection_row, error)
            logger.warning("meta form webhook: lead fetch failed: %s", error)
            # The lead exists at Meta and we could not read it. Recorded so it
            # is recoverable: the reconciliation sweep re-fetches by id, and
            # until then the practice can see a lead is outstanding.
            await record_unprocessed_submission(
                session,
                form=form,
                external_submission_id=leadgen_id,
                status=FormSubmissionStatus.FAILED.value,
                reason=f"Could not read this lead from Meta: {error}",
            )
            return []

        return await _land_and_prepare(
            session, form=form, submission=submission, raw_body=None
        )


async def _resolve_meta_form(
    session: Any,
    *,
    institution_id: str,
    connection_id: str,
    form_external_id: str | None,
):
    """The enabled form this lead belongs to, or None.

    Meta names the form in most deliveries but not all. With no name, a Page
    running exactly one enabled form is unambiguous and is used; a Page running
    several is not guessed at, because picking the wrong one would apply the
    wrong field map to somebody's answers.
    """
    stmt = select(FormDefinition).where(
        FormDefinition.institution_id == institution_id,
        FormDefinition.connection_id == connection_id,
        FormDefinition.is_enabled.is_(True),
        FormDefinition.archived_at.is_(None),
    )
    if form_external_id:
        stmt = stmt.where(FormDefinition.external_form_id == form_external_id)
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) == 1:
        return rows[0], None
    if len(rows) > 1:
        logger.warning(
            "meta form webhook: %d enabled forms matched and the delivery named none",
            len(rows),
        )
        return None, None

    # Nothing enabled matched. Meta subscribes a whole Page, so leads from the
    # practice's other forms on that Page arrive here too. When the delivery
    # names one we know about, say so against that form rather than dropping it
    # into a log.
    if form_external_id:
        known = (
            await session.execute(
                select(FormDefinition).where(
                    FormDefinition.institution_id == institution_id,
                    FormDefinition.connection_id == connection_id,
                    FormDefinition.external_form_id == form_external_id,
                )
            )
        ).scalars().first()
        return None, known
    return None, None


# ---------------------------------------------------------------------------
# Typeform
# ---------------------------------------------------------------------------
@router.post("/typeform/{form_id}")
@limiter.limit(RATE)
async def typeform_webhook(
    request: Request,
    form_id: str = Path(..., min_length=8, max_length=64),
    typeform_signature: str | None = Header(default=None, alias="Typeform-Signature"),
) -> JSONResponse:
    """One ``form_response``, for the form this URL was registered on."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _json({"error": "payload_too_large"}, 413)

    async with get_system_db_session(LOOKUP_CONTEXT, external_id=form_id) as session:
        row = (
            (
                await session.execute(
                    select(FormDefinition).where(FormDefinition.id == form_id)
                )
            )
            .scalars()
            .first()
        )
        institution_id = str(row.institution_id) if row else None
        secret = row.webhook_secret if row else None
        enabled = bool(row and row.is_enabled and row.archived_at is None)

    # One answer for an unknown form and one with no secret alike. A different
    # reply for each would let anybody who can reach the URL map which forms
    # exist. Being switched off is deliberately *not* in this branch — that is
    # decided after the signature proves the delivery is genuinely Typeform's,
    # so a real submission for a paused form can be recorded rather than lost.
    if not institution_id or not secret:
        logger.info("typeform webhook rejected: form=%s", form_id)
        return _json({"error": "unauthorised"}, 401)

    if not typeform_provider.verify_webhook_signature(
        raw, typeform_signature, secret
    ):
        logger.info("typeform webhook signature mismatch: form=%s", form_id)
        return _json({"error": "unauthorised"}, 401)

    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return _json({"error": "invalid_payload"}, 400)

    submission = typeform_provider.normalize_submission(payload)

    dispatches: list[FormWorkflowDispatch] = []
    async with get_system_db_session(
        WEBHOOK_CONTEXT, institution_id=institution_id, external_id=form_id
    ) as session:
        form = await session.get(FormDefinition, form_id)
        if form is None:
            return _json({"error": "unauthorised"}, 401)
        if not enabled:
            # Genuine, signed, and for a form the practice has switched off.
            # Recorded against that form so they can see submissions are still
            # coming in — and switch it back on rather than wonder.
            await record_unprocessed_submission(
                session,
                form=form,
                external_submission_id=submission.external_submission_id,
                status=FormSubmissionStatus.DROPPED.value,
                reason=(
                    "A response arrived while this form was switched off. "
                    "Switch it on to start bringing these in."
                ),
                submitted_at=submission.submitted_at,
                raw_body=raw,
            )
            return _json(_ACCEPTED, 202)
        dispatches = await _land_and_prepare(
            session, form=form, submission=submission, raw_body=raw
        )

    _enqueue(dispatches)
    return _json(_ACCEPTED, 202)


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
async def _land_and_prepare(
    session: Any, *, form: FormDefinition, submission: Any, raw_body: bytes | None
) -> list[FormWorkflowDispatch]:
    """Record the submission and work out which workflows it starts."""
    try:
        landed = await land_submission(
            session, form=form, submission=submission, raw_body=raw_body
        )
    except SubmissionRejected as error:
        # A mapping problem, not a transient one. Recorded against the form so
        # the practice sees "3 leads could not be processed" instead of leads
        # quietly never appearing. Never logged with what somebody submitted.
        logger.warning("form submission rejected form=%s: %s", form.id, error)
        await record_unprocessed_submission(
            session,
            form=form,
            external_submission_id=error.external_submission_id,
            status=FormSubmissionStatus.DROPPED.value,
            reason=str(error),
            raw_body=raw_body,
        )
        return []

    if landed is None:
        # Already landed by an earlier delivery. Enrolling again here is exactly
        # what the idempotency claim exists to prevent.
        return []

    context = submission_trigger_context(form=form, landed=landed)
    return await FormTriggerService(session).prepare_dispatches(
        institution_id=str(form.institution_id),
        location_id=str(form.location_id) if form.location_id else None,
        contact_id=str(landed.contact.id),
        submission_id=str(landed.submission.id),
        context=context,
    )


def _enqueue(dispatches: list[FormWorkflowDispatch]) -> None:
    """Enqueue after the transaction, so a worker cannot read a row that is
    still uncommitted — or worse, one that rolled back."""
    if not dispatches:
        return
    try:
        count = enqueue_form_workflow_dispatches(dispatches)
    except Exception:  # noqa: BLE001 — the submission is landed either way
        logger.exception("form webhook: workflow enqueue failed")
        return
    if count:
        logger.info("form webhook enqueued %d workflow(s)", count)
