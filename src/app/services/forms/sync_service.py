"""Pulling a connected account's forms in, and keeping them current.

Syncing is not a one-off import. A form's questions change — an author adds
"Have you visited before?" three weeks after the campaign launched — and the
mapping screen has to show that without losing what the clinic already decided
about the other questions.

So a sync is a diff, not a replace:

* A form the provider still lists is updated in place. Its id, its mappings and
  any workflow pointing at it survive.
* A newly discovered question gets a proposed mapping row. Existing rows are
  left exactly as they are, including the ones the clinic changed.
* A question the provider no longer lists keeps its mapping row. Deleting it
  would silently discard a decision, and the row costs nothing; it simply stops
  matching any answer.
* A form the provider stops listing is archived, not deleted, so past
  submissions and live workflows still resolve a name for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.form_integration import (
    FormDefinition,
    FormFieldMapping,
    FormProviderConnection,
)
from src.app.services.forms.connection_service import (
    account_from_connection,
    client_for,
    mark_connection_failure,
)
from src.app.services.forms.mapping_service import build_default_mapping
from src.app.services.forms.providers.base import (
    FormProviderError,
    ProviderForm,
    ProviderFormField,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    discovered: int = 0
    created: int = 0
    updated: int = 0
    archived: int = 0
    new_fields: int = 0


async def sync_connection(
    session: AsyncSession, connection: FormProviderConnection
) -> SyncResult:
    """Refresh every form on one connected account.

    A provider failure is recorded on the connection and re-raised: the caller
    turns it into a message, and the stored reason is what the settings screen
    shows next time somebody wonders why the list is stale.
    """
    account = account_from_connection(connection)
    client = client_for(connection.provider)
    try:
        forms = await client.list_forms(account)
    except FormProviderError as error:
        mark_connection_failure(connection, error)
        await session.flush()
        raise

    result = SyncResult(discovered=len(forms))
    seen: set[str] = set()

    for form in forms:
        if not form.external_id:
            continue
        seen.add(form.external_id)
        created, new_fields = await _upsert_form(
            session, connection=connection, form=form
        )
        result.new_fields += new_fields
        if created:
            result.created += 1
        else:
            result.updated += 1

    result.archived = await _archive_missing(
        session, connection=connection, seen_external_ids=seen
    )

    connection.last_synced_at = datetime.now(timezone.utc)
    connection.last_error = None
    await session.flush()
    return result


async def _upsert_form(
    session: AsyncSession,
    *,
    connection: FormProviderConnection,
    form: ProviderForm,
) -> tuple[bool, int]:
    existing = (
        await session.execute(
            select(FormDefinition).where(
                FormDefinition.institution_id == connection.institution_id,
                FormDefinition.provider == connection.provider,
                FormDefinition.external_form_id == form.external_id,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    created = existing is None
    row = existing or FormDefinition(
        institution_id=connection.institution_id,
        connection_id=connection.id,
        provider=connection.provider,
        external_form_id=form.external_id,
        name=form.name,
        # Off until somebody looks at the mapping. Syncing an account must not
        # start landing leads from forms nobody has reviewed.
        is_enabled=False,
    )
    row.name = form.name or row.name
    # A form re-authorised through a different connection follows the live one,
    # otherwise its submissions would be fetched with a dead token.
    row.connection_id = connection.id
    row.fields = [
        {
            "key": item.key,
            "label": item.label,
            "type": item.type,
            "options": list(item.options),
        }
        for item in form.fields
    ]
    row.last_synced_at = now
    row.archived_at = None
    if created:
        session.add(row)
    await session.flush()

    new_fields = await _ensure_mappings(session, form_row=row, fields=form.fields)
    return created, new_fields


async def _ensure_mappings(
    session: AsyncSession,
    *,
    form_row: FormDefinition,
    fields: list[ProviderFormField],
) -> int:
    """Propose a mapping for each question we have not seen before."""
    existing_keys = set(
        (
            await session.execute(
                select(FormFieldMapping.source_key).where(
                    FormFieldMapping.form_id == form_row.id
                )
            )
        )
        .scalars()
        .all()
    )

    added = 0
    for item in fields:
        if item.key in existing_keys:
            # The clinic may have changed this mapping; sync never overrules it.
            # Only the wording and type are refreshed, and only for display.
            await _refresh_source_metadata(session, form_row=form_row, item=item)
            continue
        session.add(
            build_default_mapping(
                institution_id=form_row.institution_id,
                form_id=str(form_row.id),
                source=item,
            )
        )
        added += 1
    if added:
        await session.flush()
    return added


async def _refresh_source_metadata(
    session: AsyncSession, *, form_row: FormDefinition, item: ProviderFormField
) -> None:
    row = (
        await session.execute(
            select(FormFieldMapping).where(
                FormFieldMapping.form_id == form_row.id,
                FormFieldMapping.source_key == item.key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.source_label = item.label or row.source_label
    row.source_type = item.type or row.source_type


async def _archive_missing(
    session: AsyncSession,
    *,
    connection: FormProviderConnection,
    seen_external_ids: set[str],
) -> int:
    """Mark forms the provider no longer lists, and stop accepting them.

    Disabling matters as much as the timestamp: an archived form should not keep
    landing leads if a stale webhook delivery arrives afterwards.
    """
    rows = (
        (
            await session.execute(
                select(FormDefinition).where(
                    FormDefinition.connection_id == connection.id,
                    FormDefinition.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    archived = 0
    for row in rows:
        if row.external_form_id in seen_external_ids:
            continue
        row.archived_at = now
        row.is_enabled = False
        archived += 1
    if archived:
        await session.flush()
    return archived
