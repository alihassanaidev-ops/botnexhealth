"""RLS on the lead-form tables, enforced by a real PostgreSQL.

The static test asserts the migration *says* the right thing. This one asserts
PostgreSQL *does* it, which is the only claim that matters for a surface with
these properties:

* two of the tables are reachable from an unauthenticated endpoint;
* that endpoint resolves the tenant from data the caller supplied;
* the rows hold a provider access token and a stranger's contact details.

Runs the real Alembic chain to head against a throwaway container and connects
as a non-superuser, because a superuser bypasses RLS entirely and would make
every one of these tests pass for the wrong reason.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# The harness is shared with the main RLS suite rather than copied: a second
# copy would drift, and the point of walking the real chain is that it stays
# honest as migrations land.
from tests.integration.test_rls_postgres import (
    INST_A,
    INST_B,
    LOC_A1,
    USER_ADMIN_A,
    USER_STAFF_A1,
    _apply_rls_migration,
    _create_app_role,
    _database_url_with_credentials,
    _seed,
    _set_context,
)

pytestmark = pytest.mark.rls

CONN_A = "30000000-0000-0000-0000-00000000000a"
CONN_B = "30000000-0000-0000-0000-00000000000b"
FORM_A = "31000000-0000-0000-0000-00000000000a"
FORM_B = "31000000-0000-0000-0000-00000000000b"
SUB_A = "32000000-0000-0000-0000-00000000000a"

PAGE_A = "page-aaa-111"
PAGE_B = "page-bbb-222"


@pytest.fixture(scope="module")
def form_database_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    PostgresContainer = postgres_module.PostgresContainer

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - depends on local Docker
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")

    try:
        raw = container.get_connection_url()
        yield raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1).replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="module")
async def form_engine(form_database_url: str):
    await _apply_rls_migration(form_database_url)
    await _create_app_role(form_database_url)

    engine = create_async_engine(
        _database_url_with_credentials(
            form_database_url, username="rls_app", password="rls_app"
        ),
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await _set_context(conn, role="SUPER_ADMIN", user_id="super")
        await _seed(conn)
        await _seed_forms(conn)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_forms(conn) -> None:
    """One connected account and one form in each of two institutions.

    Timestamps are supplied explicitly: on a fresh database the baseline's
    ``create_all`` builds these tables from the models, where ``created_at`` is
    a Python-side default rather than a server one. The application always
    writes through the models, so this only affects raw SQL like this seed.
    """
    await conn.execute(
        text(
            """
            INSERT INTO form_provider_connections
              (id, institution_id, provider, account_ref, account_name, status,
               created_at, updated_at)
            VALUES
              (:conn_a, :inst_a, 'meta', :page_a, 'Clinic A Page', 'active',
               now(), now()),
              (:conn_b, :inst_b, 'meta', :page_b, 'Clinic B Page', 'active',
               now(), now())
            """
        ),
        {
            "conn_a": CONN_A,
            "conn_b": CONN_B,
            "inst_a": INST_A,
            "inst_b": INST_B,
            "page_a": PAGE_A,
            "page_b": PAGE_B,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO form_definitions
              (id, institution_id, connection_id, provider, external_form_id,
               name, location_id, is_enabled, source_name, webhook_status,
               consent_sms, consent_email, created_at, updated_at)
            VALUES
              (:form_a, :inst_a, :conn_a, 'meta', 'ext-a', 'Clinic A Form',
               :loc_a1, true, 'external_form', 'registered', false, false,
               now(), now()),
              (:form_b, :inst_b, :conn_b, 'meta', 'ext-b', 'Clinic B Form',
               NULL, true, 'external_form', 'registered', false, false,
               now(), now())
            """
        ),
        {
            "form_a": FORM_A,
            "form_b": FORM_B,
            "inst_a": INST_A,
            "inst_b": INST_B,
            "conn_a": CONN_A,
            "conn_b": CONN_B,
            "loc_a1": LOC_A1,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO form_submissions
              (id, institution_id, form_id, external_submission_id, status,
               received_at)
            VALUES (:sub_a, :inst_a, :form_a, 'response-1', 'processed', now())
            """
        ),
        {"sub_a": SUB_A, "inst_a": INST_A, "form_a": FORM_A},
    )


async def _count(conn, table: str) -> int:
    result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
    return int(result.scalar_one())


# ── tenant isolation ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_admin_sees_only_their_own_practices_connections(form_engine) -> None:
    async with form_engine.connect() as conn:
        await _set_context(
            conn,
            role="INSTITUTION_ADMIN",
            user_id=USER_ADMIN_A,
            institution_id=INST_A,
        )
        rows = (
            await conn.execute(
                text("SELECT institution_id FROM form_provider_connections")
            )
        ).scalars().all()
    assert [str(row) for row in rows] == [INST_A]


@pytest.mark.asyncio
async def test_an_admin_cannot_write_into_another_practice(form_engine) -> None:
    """The token in these rows makes cross-tenant insert the worst case here."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn,
            role="INSTITUTION_ADMIN",
            user_id=USER_ADMIN_A,
            institution_id=INST_A,
        )
        with pytest.raises(DBAPIError):
            await conn.execute(
                text(
                    "INSERT INTO form_provider_connections "
                    "(id, institution_id, provider, account_ref, status, "
                    "created_at, updated_at) VALUES "
                    "(gen_random_uuid(), :inst_b, 'meta', 'stolen-page', "
                    "'active', now(), now())"
                ),
                {"inst_b": INST_B},
            )


@pytest.mark.asyncio
async def test_staff_may_read_form_names_but_not_change_them(form_engine) -> None:
    """The builder shows form names to whoever edits a workflow; only admins
    decide where a stranger's contact details land."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn, role="STAFF", user_id=USER_STAFF_A1, institution_id=INST_A
        )
        assert await _count(conn, "form_definitions") == 1
        # A write under a SELECT-only policy is filtered to nothing rather than
        # raising — Postgres hides the rows instead of refusing the statement.
        # Zero rows affected *is* the refusal.
        result = await conn.execute(
            text("UPDATE form_definitions SET is_enabled = false")
        )
        assert result.rowcount == 0


@pytest.mark.asyncio
async def test_staff_cannot_read_the_stored_provider_token(form_engine) -> None:
    async with form_engine.connect() as conn:
        await _set_context(
            conn, role="STAFF", user_id=USER_STAFF_A1, institution_id=INST_A
        )
        assert await _count(conn, "form_provider_connections") == 0


# ── pre-tenant webhook lookup ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_lookup_context_sees_only_the_page_it_names(form_engine) -> None:
    """Meta names the clinic only by Page id, before any tenant exists."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook_lookup", external_id=PAGE_A
        )
        rows = (
            await conn.execute(
                text("SELECT account_ref FROM form_provider_connections")
            )
        ).scalars().all()
    assert rows == [PAGE_A]


@pytest.mark.asyncio
async def test_the_lookup_context_sees_only_the_form_it_names(form_engine) -> None:
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook_lookup", external_id=FORM_A
        )
        rows = (
            await conn.execute(text("SELECT id FROM form_definitions"))
        ).scalars().all()
    assert [str(row) for row in rows] == [FORM_A]


@pytest.mark.asyncio
async def test_the_lookup_context_reaches_nothing_else(form_engine) -> None:
    """It resolves one row and then hands over. Contacts, consent and landed
    submissions are all outside it."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook_lookup", external_id=PAGE_A
        )
        for table in ("form_submissions", "form_field_mappings", "contacts"):
            assert await _count(conn, table) == 0


@pytest.mark.asyncio
async def test_the_lookup_context_cannot_write(form_engine) -> None:
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook_lookup", external_id=PAGE_A
        )
        result = await conn.execute(
            text(
                "UPDATE form_provider_connections SET account_name = 'x' "
                "WHERE account_ref = :ref"
            ),
            {"ref": PAGE_A},
        )
        # It can read this one row and change nothing, which is the whole
        # remit: resolve the tenant, then hand over.
        assert result.rowcount == 0


# ── verified webhook context ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_webhook_context_is_confined_to_its_institution(form_engine) -> None:
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook", institution_id=INST_A
        )
        rows = (
            await conn.execute(text("SELECT institution_id FROM form_submissions"))
        ).scalars().all()
    assert [str(row) for row in rows] == [INST_A]


@pytest.mark.asyncio
async def test_the_webhook_context_cannot_land_into_another_practice(
    form_engine,
) -> None:
    """A forged or confused Page id must not become a contact in someone
    else's records."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook", institution_id=INST_A
        )
        with pytest.raises(DBAPIError):
            await conn.execute(
                text(
                    "INSERT INTO form_submissions "
                    "(id, institution_id, form_id, external_submission_id, "
                    "status, received_at) VALUES "
                    "(gen_random_uuid(), :inst_b, :form_b, 'x', 'received', now())"
                ),
                {"inst_b": INST_B, "form_b": FORM_B},
            )


@pytest.mark.asyncio
async def test_the_webhook_context_may_read_workflows_but_not_edit_them(
    form_engine,
) -> None:
    """A delivery enrols workflows. It has no business changing one."""
    async with form_engine.connect() as conn:
        await _set_context(
            conn, context_type="form_webhook", institution_id=INST_A
        )
        await conn.execute(text("SELECT count(*) FROM automation_workflows"))
        result = await conn.execute(text("UPDATE automation_workflows SET name = 'x'"))
        assert result.rowcount == 0
