"""Connecting form providers, syncing forms, and saying what each question means.

The three screens behind this, in the order a clinic meets them:

1. **Connect.** Authorise Meta or Typeform. The redirect leaves our origin, so
   everything needed on the way back travels in a signed, expiring ``state``;
   the callback checks it against the caller's own institution before writing
   anything, which is what stops a callback being replayed into another tenant.
2. **Sync.** Pull the account's forms and their questions.
3. **Map, then enable.** Say which question is the email, which is the phone,
   and which are the qualification answers a workflow will branch on. Only then
   can the form be switched on — a form enabled with no contact method would
   accept submissions it can do nothing with.

Reading is open to any signed-in user of the clinic, because the workflow
builder has to show form names and answer keys to whoever is editing a workflow.
Writing is institution-admin only: these rows hold a provider access token and
decide where a stranger's contact details land.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import (
    get_current_institution_admin,
    get_current_institution_or_location_user,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.config import settings
from src.app.database import get_db_session_dep
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.custom_field import CustomFieldDefinition, EntityType
from src.app.models.form_integration import (
    CONTACT_FIELD_KEYS,
    FormConnectionStatus,
    FormDefinition,
    FormFieldMapping,
    FormFieldTarget,
    FormProvider,
    FormProviderConnection,
    FormSubmission,
    FormSubmissionStatus,
    FormWebhookStatus,
    generate_webhook_secret,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User
from src.app.services.audit_decorator import audit
from src.app.services.forms import connection_service, sync_service
from src.app.services.forms.mapping_service import slugify
from src.app.services.forms.providers.base import FormProviderError
from src.app.services.forms.providers.typeform import PROVIDER as TYPEFORM

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/form-integrations", tags=["Form Integrations"])

_Admin = Annotated[User, Depends(get_current_institution_admin)]
_Reader = Annotated[User, Depends(get_current_institution_or_location_user)]
_Session = Annotated[AsyncSession, Depends(get_db_session_dep)]

_PROVIDERS = tuple(provider.value for provider in FormProvider)


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------
class ProviderStatus(BaseModel):
    provider: str
    label: str
    #: False when the deployment has no OAuth app for this provider. The screen
    #: says so instead of offering a Connect button that dies at the redirect.
    configured: bool
    connection_count: int


class ConnectionResponse(BaseModel):
    id: str
    provider: str
    account_ref: str
    account_name: str | None
    status: str
    granted_scopes: str | None
    token_expires_at: datetime | None
    last_synced_at: datetime | None
    last_error: str | None
    form_count: int
    created_at: datetime
    #: Set when the practice disconnected the account. The row and its history
    #: are kept; reconnecting the same account revives it.
    disconnected_at: datetime | None = None


class FieldMappingResponse(BaseModel):
    id: str
    source_key: str
    source_label: str | None
    source_type: str | None
    target_kind: str
    target_contact_field: str | None
    target_custom_field_id: str | None
    #: Where this answer shows up in a workflow's context, or null when it does
    #: not travel there at all.
    context_key: str | None


class FormSummary(BaseModel):
    id: str
    provider: str
    external_form_id: str
    name: str
    location_id: str | None
    is_enabled: bool
    source_name: str
    webhook_status: str
    webhook_last_error: str | None
    consent_sms: bool
    consent_email: bool
    archived_at: datetime | None
    last_submission_at: datetime | None
    last_synced_at: datetime | None
    connection_id: str
    #: Keys a workflow can branch on, so the builder can offer them without a
    #: second round trip per form.
    context_keys: list[str] = Field(default_factory=list)
    #: Submissions that arrived and did not become a contact. The whole point
    #: of counting them here is that a clinic sees leads were lost.
    unprocessed_count: int = 0
    #: Why the most recent one was not processed.
    last_issue: str | None = None


class FormDetail(FormSummary):
    fields: list[dict[str, Any]] = Field(default_factory=list)
    mappings: list[FieldMappingResponse] = Field(default_factory=list)


class SubmissionSummary(BaseModel):
    id: str
    external_submission_id: str
    contact_id: str | None
    status: str
    error_summary: str | None
    #: Only the non-identifying mapped answers. The rest is on the contact.
    context_answers: dict[str, Any] | None
    submitted_at: datetime | None
    received_at: datetime


class SyncResponse(BaseModel):
    discovered: int
    created: int
    updated: int
    archived: int
    new_fields: int


class OAuthStartRequest(BaseModel):
    provider: str


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=2000)
    state: str = Field(..., min_length=1, max_length=2000)


class OAuthCallbackResponse(BaseModel):
    provider: str
    connections: list[ConnectionResponse]


class FormUpdate(BaseModel):
    is_enabled: bool | None = None
    location_id: str | None = None
    source_name: str | None = Field(default=None, max_length=80)
    consent_sms: bool | None = None
    consent_email: bool | None = None
    consent_wording: str | None = Field(default=None, max_length=2000)


class MappingUpsert(BaseModel):
    source_key: str = Field(..., min_length=1, max_length=200)
    target_kind: str
    target_contact_field: str | None = None
    target_custom_field_id: str | None = None


class MappingsUpdate(BaseModel):
    mappings: list[MappingUpsert]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No institution")
    return str(user.institution_id)


def _provider_or_400(provider: str) -> str:
    if provider not in _PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown provider")
    return provider


def _provider_error(error: FormProviderError) -> HTTPException:
    """Provider wording, passed through.

    A clinic that has to fix something in Meta needs Meta's own message; a
    generic "sync failed" sends them to support instead of to the setting.
    """
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(error))


async def _connection_or_404(
    session: AsyncSession, connection_id: str, institution_id: str
) -> FormProviderConnection:
    row = (
        await session.execute(
            select(FormProviderConnection).where(
                FormProviderConnection.id == connection_id,
                FormProviderConnection.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    return row


async def _form_or_404(
    session: AsyncSession, form_id: str, institution_id: str
) -> FormDefinition:
    row = (
        await session.execute(
            select(FormDefinition).where(
                FormDefinition.id == form_id,
                FormDefinition.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    return row


#: Statuses meaning "arrived and did not become a contact".
_UNPROCESSED_STATUSES = (
    FormSubmissionStatus.DROPPED.value,
    FormSubmissionStatus.FAILED.value,
)


async def _unprocessed(
    session: AsyncSession, form_ids: list[str]
) -> dict[str, tuple[int, str | None]]:
    """Per form: how many submissions were not processed, and the latest reason."""
    if not form_ids:
        return {}
    rows = (
        await session.execute(
            select(
                FormSubmission.form_id,
                FormSubmission.error_summary,
                FormSubmission.received_at,
            )
            .where(
                FormSubmission.form_id.in_(form_ids),
                FormSubmission.status.in_(_UNPROCESSED_STATUSES),
            )
            .order_by(FormSubmission.received_at.desc())
        )
    ).all()
    summary: dict[str, tuple[int, str | None]] = {}
    for form_id, error_summary, _received_at in rows:
        key = str(form_id)
        count, latest = summary.get(key, (0, None))
        # Rows arrive newest first, so the first reason seen is the latest one.
        summary[key] = (count + 1, latest or error_summary)
    return summary


async def _context_keys(session: AsyncSession, form_id: str) -> list[str]:
    rows = (
        (
            await session.execute(
                select(FormFieldMapping.context_key).where(
                    FormFieldMapping.form_id == form_id,
                    FormFieldMapping.context_key.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted({key for key in rows if key})


def _form_summary(
    row: FormDefinition,
    context_keys: list[str],
    *,
    unprocessed_count: int = 0,
    last_issue: str | None = None,
) -> FormSummary:
    return FormSummary(
        id=str(row.id),
        provider=row.provider,
        external_form_id=row.external_form_id,
        name=row.name,
        location_id=str(row.location_id) if row.location_id else None,
        is_enabled=row.is_enabled,
        source_name=row.source_name,
        webhook_status=row.webhook_status,
        webhook_last_error=row.webhook_last_error,
        consent_sms=row.consent_sms,
        consent_email=row.consent_email,
        archived_at=row.archived_at,
        last_submission_at=row.last_submission_at,
        last_synced_at=row.last_synced_at,
        connection_id=str(row.connection_id),
        context_keys=context_keys,
        unprocessed_count=unprocessed_count,
        last_issue=last_issue,
    )


def _mapping_response(row: FormFieldMapping) -> FieldMappingResponse:
    return FieldMappingResponse(
        id=str(row.id),
        source_key=row.source_key,
        source_label=row.source_label,
        source_type=row.source_type,
        target_kind=row.target_kind,
        target_contact_field=row.target_contact_field,
        target_custom_field_id=(
            str(row.target_custom_field_id) if row.target_custom_field_id else None
        ),
        context_key=row.context_key,
    )


# ---------------------------------------------------------------------------
# Providers and connections
# ---------------------------------------------------------------------------
@router.get("/providers", response_model=list[ProviderStatus])
@limiter.limit(RATE_READ)
async def list_providers(
    request: Request, current_user: _Reader, session: _Session
) -> list[ProviderStatus]:
    """Which providers this deployment can offer, and what is connected."""
    institution_id = _institution_id(current_user)
    counts = dict(
        (
            await session.execute(
                select(
                    FormProviderConnection.provider,
                    func.count(FormProviderConnection.id),
                )
                .where(
                    FormProviderConnection.institution_id == institution_id,
                    FormProviderConnection.disconnected_at.is_(None),
                )
                .group_by(FormProviderConnection.provider)
            )
        ).all()
    )
    labels = {
        FormProvider.META.value: "Meta Lead Ads",
        FormProvider.TYPEFORM.value: "Typeform",
    }
    return [
        ProviderStatus(
            provider=provider,
            label=labels.get(provider, provider),
            configured=connection_service.provider_is_configured(provider),
            connection_count=int(counts.get(provider, 0)),
        )
        for provider in _PROVIDERS
    ]


@router.get("/connections", response_model=list[ConnectionResponse])
@limiter.limit(RATE_READ)
async def list_connections(
    request: Request, current_user: _Reader, session: _Session
) -> list[ConnectionResponse]:
    institution_id = _institution_id(current_user)
    rows = (
        (
            await session.execute(
                select(FormProviderConnection)
                .where(FormProviderConnection.institution_id == institution_id)
                .order_by(FormProviderConnection.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(FormDefinition.connection_id, func.count(FormDefinition.id))
                .where(FormDefinition.institution_id == institution_id)
                .group_by(FormDefinition.connection_id)
            )
        ).all()
    )
    return [_connection_response(row, int(counts.get(row.id, 0))) for row in rows]


def _connection_response(
    row: FormProviderConnection, form_count: int
) -> ConnectionResponse:
    return ConnectionResponse(
        id=str(row.id),
        provider=row.provider,
        account_ref=row.account_ref,
        account_name=row.account_name,
        status=row.status,
        granted_scopes=row.granted_scopes,
        token_expires_at=row.token_expires_at,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        form_count=form_count,
        created_at=row.created_at,
        disconnected_at=row.disconnected_at,
    )


@router.post("/oauth/start", response_model=OAuthStartResponse)
@limiter.limit(RATE_WRITE)
async def start_oauth(
    request: Request,
    data: OAuthStartRequest,
    current_user: _Admin,
    session: _Session,
) -> OAuthStartResponse:
    """Where to send the clinic to authorise, with a signed state to come back with."""
    provider = _provider_or_400(data.provider)
    if not connection_service.provider_is_configured(provider):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This provider is not configured on this deployment.",
        )
    state = connection_service.encode_state(
        provider=provider,
        institution_id=_institution_id(current_user),
        user_id=str(current_user.id),
    )
    try:
        url = connection_service.authorization_url(provider=provider, state=state)
    except FormProviderError as error:
        raise _provider_error(error) from error
    return OAuthStartResponse(authorization_url=url, state=state)


@router.post("/oauth/callback", response_model=OAuthCallbackResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_CREATE,
    resource=lambda *a, **kw: "form_integration:connect",
    actor=AuditActor.ADMIN,
)
async def complete_oauth(
    request: Request,
    data: OAuthCallbackRequest,
    current_user: _Admin,
    session: _Session,
) -> OAuthCallbackResponse:
    """Finish the authorisation and store the account(s) it granted."""
    try:
        state = connection_service.decode_state(data.state)
    except FormProviderError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    institution_id = _institution_id(current_user)
    if state.institution_id != institution_id:
        # The state was minted for a different clinic. Refused rather than
        # honoured under the caller's own tenant: a state that travels through
        # a browser is exactly what a cross-tenant replay would reuse.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This connection link was not issued for your practice.",
        )

    try:
        accounts = await connection_service.exchange_code_for_accounts(
            provider=state.provider, code=data.code
        )
    except FormProviderError as error:
        raise _provider_error(error) from error

    stored = []
    for account in accounts:
        row = await connection_service.upsert_connection(
            session,
            institution_id=institution_id,
            provider=state.provider,
            account=account,
            user_id=str(current_user.id),
        )
        stored.append(_connection_response(row, 0))

    return OAuthCallbackResponse(provider=state.provider, connections=stored)


@router.post("/connections/{connection_id}/sync", response_model=SyncResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"form_integration:sync:{kw.get('connection_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def sync_connection(
    request: Request,
    connection_id: str,
    current_user: _Admin,
    session: _Session,
) -> SyncResponse:
    """Refresh this account's forms and their questions."""
    connection = await _connection_or_404(
        session, connection_id, _institution_id(current_user)
    )
    try:
        result = await sync_service.sync_connection(session, connection)
    except FormProviderError as error:
        raise _provider_error(error) from error
    return SyncResponse(
        discovered=result.discovered,
        created=result.created,
        updated=result.updated,
        archived=result.archived,
        new_fields=result.new_fields,
    )


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"form_integration:disconnect:{kw.get('connection_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def disconnect(
    request: Request,
    connection_id: str,
    current_user: _Admin,
    session: _Session,
) -> None:
    """Revoke an account, keeping the record of what it brought in.

    Deliberately a soft delete. Deleting the row would cascade through the
    forms, their field maps and every landed submission — so disconnecting an
    account would silently destroy the history of who came in through it, and
    reconnecting would not bring it back. Instead the token is discarded, the
    forms are switched off, and the row is marked.

    Delivery is stopped at the provider first, best effort. If that call fails
    the disconnect still happens: a clinic asking to disconnect must not be
    blocked by the provider being unreachable, and a submission arriving for a
    switched-off form is recorded rather than acted on.
    """
    connection = await _connection_or_404(
        session, connection_id, _institution_id(current_user)
    )
    forms = (
        (
            await session.execute(
                select(FormDefinition).where(
                    FormDefinition.connection_id == connection.id
                )
            )
        )
        .scalars()
        .all()
    )
    try:
        client = connection_service.client_for(connection.provider)
        account = connection_service.account_from_connection(connection)
        for form in forms:
            if form.webhook_status == FormWebhookStatus.REGISTERED.value:
                await client.unregister_webhook(account, form.external_form_id)
    except FormProviderError:
        logger.info(
            "form integration: provider cleanup skipped for connection=%s",
            connection_id,
        )

    for form in forms:
        form.is_enabled = False
        form.webhook_status = FormWebhookStatus.NONE.value

    connection.status = FormConnectionStatus.REVOKED.value
    connection.disconnected_at = datetime.now(timezone.utc)
    # The credential is what actually has to go. Everything else is history.
    connection.access_token = None
    connection.refresh_token = None
    connection.token_expires_at = None
    connection.last_error = None
    await session.flush()


# ---------------------------------------------------------------------------
# Forms and mappings
# ---------------------------------------------------------------------------
@router.get("/forms", response_model=list[FormSummary])
@limiter.limit(RATE_READ)
async def list_forms(
    request: Request,
    current_user: _Reader,
    session: _Session,
    provider: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
) -> list[FormSummary]:
    """Every synced form. The workflow builder's trigger picker reads this."""
    institution_id = _institution_id(current_user)
    stmt = select(FormDefinition).where(
        FormDefinition.institution_id == institution_id
    )
    if provider:
        stmt = stmt.where(FormDefinition.provider == _provider_or_400(provider))
    if enabled_only:
        stmt = stmt.where(
            FormDefinition.is_enabled.is_(True),
            FormDefinition.archived_at.is_(None),
        )
    rows = (
        (await session.execute(stmt.order_by(FormDefinition.name))).scalars().all()
    )

    keys_by_form: dict[str, list[str]] = {}
    if rows:
        mapped = (
            await session.execute(
                select(FormFieldMapping.form_id, FormFieldMapping.context_key).where(
                    FormFieldMapping.form_id.in_([row.id for row in rows]),
                    FormFieldMapping.context_key.is_not(None),
                )
            )
        ).all()
        for form_id, context_key in mapped:
            if context_key:
                keys_by_form.setdefault(str(form_id), []).append(context_key)

    unprocessed = await _unprocessed(session, [str(row.id) for row in rows])
    return [
        _form_summary(
            row,
            sorted(set(keys_by_form.get(str(row.id), []))),
            unprocessed_count=unprocessed.get(str(row.id), (0, None))[0],
            last_issue=unprocessed.get(str(row.id), (0, None))[1],
        )
        for row in rows
    ]


async def _form_detail(
    session: AsyncSession, *, form_id: str, institution_id: str
) -> FormDetail:
    """One form: what it asks, and what each question currently means here."""
    row = await _form_or_404(session, form_id, institution_id)
    mappings = (
        (
            await session.execute(
                select(FormFieldMapping)
                .where(FormFieldMapping.form_id == row.id)
                .order_by(FormFieldMapping.source_key)
            )
        )
        .scalars()
        .all()
    )
    count, last_issue = (await _unprocessed(session, [str(row.id)])).get(
        str(row.id), (0, None)
    )
    summary = _form_summary(
        row,
        sorted({m.context_key for m in mappings if m.context_key}),
        unprocessed_count=count,
        last_issue=last_issue,
    )
    return FormDetail(
        **summary.model_dump(),
        fields=list(row.fields or []),
        mappings=[_mapping_response(mapping) for mapping in mappings],
    )


@router.get("/forms/{form_id}", response_model=FormDetail)
@limiter.limit(RATE_READ)
async def get_form(
    request: Request,
    form_id: str,
    current_user: _Reader,
    session: _Session,
) -> FormDetail:
    return await _form_detail(
        session, form_id=form_id, institution_id=_institution_id(current_user)
    )


@router.get("/forms/{form_id}/submissions", response_model=list[SubmissionSummary])
@limiter.limit(RATE_READ)
async def list_submissions(
    request: Request,
    form_id: str,
    current_user: _Reader,
    session: _Session,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[SubmissionSummary]:
    """The most recent submissions, so a clinic can see the mapping working.

    Carries only the non-identifying mapped answers. The person is on the
    contact record, where access is already audited.
    """
    institution_id = _institution_id(current_user)
    await _form_or_404(session, form_id, institution_id)
    rows = (
        (
            await session.execute(
                select(FormSubmission)
                .where(FormSubmission.form_id == form_id)
                .order_by(FormSubmission.received_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        SubmissionSummary(
            id=str(row.id),
            external_submission_id=row.external_submission_id,
            contact_id=str(row.contact_id) if row.contact_id else None,
            status=row.status,
            error_summary=row.error_summary,
            context_answers=row.context_answers,
            submitted_at=row.submitted_at,
            received_at=row.received_at,
        )
        for row in rows
    ]


@router.put("/forms/{form_id}/mappings", response_model=FormDetail)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"form_integration:mappings:{kw.get('form_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def update_mappings(
    request: Request,
    form_id: str,
    data: MappingsUpdate,
    current_user: _Admin,
    session: _Session,
) -> FormDetail:
    """Replace this form's field map.

    A full replace rather than a patch, because the screen edits the whole map
    at once and a partial update would leave a question in whatever state a
    previous session left it — which is how a "mapped" screen ends up describing
    something that is not what runs.
    """
    institution_id = _institution_id(current_user)
    form = await _form_or_404(session, form_id, institution_id)

    known_keys = {
        str(field.get("key"))
        for field in (form.fields or [])
        if isinstance(field, dict) and field.get("key")
    }
    custom_ids = {
        entry.target_custom_field_id
        for entry in data.mappings
        if entry.target_custom_field_id
    }
    definitions = {}
    if custom_ids:
        rows = (
            (
                await session.execute(
                    select(CustomFieldDefinition).where(
                        CustomFieldDefinition.institution_id == institution_id,
                        CustomFieldDefinition.id.in_(custom_ids),
                        CustomFieldDefinition.entity_type == EntityType.CONTACT.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        definitions = {str(row.id): row for row in rows}

    existing = {
        row.source_key: row
        for row in (
            (
                await session.execute(
                    select(FormFieldMapping).where(FormFieldMapping.form_id == form.id)
                )
            )
            .scalars()
            .all()
        )
    }

    seen: set[str] = set()
    for entry in data.mappings:
        if known_keys and entry.source_key not in known_keys:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{entry.source_key}' is not a question on this form. "
                "Sync the form and try again.",
            )
        target_kind, contact_field, custom_id, context_key = _validate_target(
            entry, definitions
        )
        seen.add(entry.source_key)

        row = existing.get(entry.source_key)
        if row is None:
            row = FormFieldMapping(
                institution_id=institution_id,
                form_id=str(form.id),
                source_key=entry.source_key,
            )
            session.add(row)
        row.target_kind = target_kind
        row.target_contact_field = contact_field
        row.target_custom_field_id = custom_id
        row.context_key = context_key

    # A question the caller left out is not implicitly kept: the screen sends
    # the whole map, so absence means "no longer mapped".
    for source_key, row in existing.items():
        if source_key in seen:
            continue
        row.target_kind = FormFieldTarget.IGNORE.value
        row.target_contact_field = None
        row.target_custom_field_id = None
        row.context_key = None

    await session.flush()
    return await _form_detail(
        session, form_id=form_id, institution_id=institution_id
    )


def _validate_target(
    entry: MappingUpsert, definitions: dict[str, CustomFieldDefinition]
) -> tuple[str, str | None, str | None, str | None]:
    kind = entry.target_kind
    if kind == FormFieldTarget.CONTACT_FIELD.value:
        target = (entry.target_contact_field or "").strip()
        if target not in CONTACT_FIELD_KEYS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Unknown contact field '{target}'"
            )
        # Identity never reaches the run context; it lives on the contact and
        # is read back through merge fields, which is the audited path.
        return kind, target, None, None
    if kind == FormFieldTarget.CUSTOM_FIELD.value:
        definition = definitions.get(str(entry.target_custom_field_id or ""))
        if definition is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That custom field does not exist on this practice's contacts.",
            )
        # A PHI custom field is still written to the contact — it just does not
        # travel into a workflow's context, so it cannot be branched on.
        context_key = None if definition.is_phi else slugify(definition.field_key)
        return kind, None, str(definition.id), context_key
    if kind == FormFieldTarget.IGNORE.value:
        return kind, None, None, None
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown target kind '{kind}'")


@router.patch("/forms/{form_id}", response_model=FormSummary)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CAMPAIGN_UPDATE,
    resource=lambda *a, **kw: f"form_integration:form:{kw.get('form_id', '?')}",
    actor=AuditActor.ADMIN,
)
async def update_form(
    request: Request,
    form_id: str,
    data: FormUpdate,
    current_user: _Admin,
    session: _Session,
) -> FormSummary:
    """Set where a form's leads land, what consent it obtained, and switch it on.

    Enabling is the moment delivery is registered with the provider, and the
    moment the mapping has to make sense — a form with no question mapped to an
    email or a phone would accept submissions nobody could act on.
    """
    institution_id = _institution_id(current_user)
    form = await _form_or_404(session, form_id, institution_id)

    if data.location_id is not None:
        if data.location_id:
            owns = (
                await session.execute(
                    select(InstitutionLocation.id).where(
                        InstitutionLocation.id == data.location_id,
                        InstitutionLocation.institution_id == institution_id,
                    )
                )
            ).scalar_one_or_none()
            if owns is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
            form.location_id = data.location_id
        else:
            form.location_id = None
    if data.source_name is not None:
        form.source_name = data.source_name.strip() or "external_form"
    if data.consent_sms is not None:
        form.consent_sms = data.consent_sms
    if data.consent_email is not None:
        form.consent_email = data.consent_email
    if data.consent_wording is not None:
        form.consent_wording = data.consent_wording.strip() or None

    if data.is_enabled is not None and data.is_enabled != form.is_enabled:
        if data.is_enabled:
            await _enable_form(session, form)
        else:
            form.is_enabled = False

    await session.flush()
    count, last_issue = (await _unprocessed(session, [str(form.id)])).get(
        str(form.id), (0, None)
    )
    return _form_summary(
        form,
        await _context_keys(session, str(form.id)),
        unprocessed_count=count,
        last_issue=last_issue,
    )


async def _enable_form(session: AsyncSession, form: FormDefinition) -> None:
    if form.archived_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This form no longer exists at the provider. Sync the account first.",
        )
    if (form.consent_sms or form.consent_email) and not form.consent_wording:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Record the consent wording the form shows before declaring consent.",
        )

    reachable = (
        await session.execute(
            select(func.count(FormFieldMapping.id)).where(
                FormFieldMapping.form_id == form.id,
                FormFieldMapping.target_kind == FormFieldTarget.CONTACT_FIELD.value,
                FormFieldMapping.target_contact_field.in_(("email", "phone")),
            )
        )
    ).scalar_one()
    if not reachable:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Map one of this form's questions to an email or phone before "
            "enabling it — otherwise nothing can act on a submission.",
        )

    connection = await session.get(FormProviderConnection, form.connection_id)
    if connection is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This form's provider connection is missing."
        )

    secret = form.webhook_secret
    if form.provider == TYPEFORM and not secret:
        secret = generate_webhook_secret()
        form.webhook_secret = secret

    try:
        client = connection_service.client_for(form.provider)
        account = connection_service.account_from_connection(connection)
        await client.register_webhook(
            account,
            form.external_form_id,
            callback_url=_webhook_url(form),
            secret=secret,
        )
    except FormProviderError as error:
        # Left disabled on purpose. A form that is on but not delivering is the
        # worst of both: it looks live and produces nothing.
        form.webhook_status = FormWebhookStatus.FAILED.value
        form.webhook_last_error = str(error)[:500]
        connection_service.mark_connection_failure(connection, error)
        await session.flush()
        raise _provider_error(error) from error

    form.webhook_status = FormWebhookStatus.REGISTERED.value
    form.webhook_last_error = None
    form.webhook_registered_at = datetime.now(timezone.utc)
    form.is_enabled = True


def _webhook_url(form: FormDefinition) -> str:
    """Where this form's submissions are delivered.

    Typeform gets a per-form URL, which is what lets each form carry its own
    signing secret. Meta has one URL for the whole app; the Page id in the body
    is what resolves the clinic.
    """
    base = (settings.public_api_url or settings.public_base_url or "").rstrip("/")
    if form.provider == TYPEFORM:
        return f"{base}/api/v1/forms/webhooks/typeform/{form.id}"
    return f"{base}/api/v1/forms/webhooks/meta"
