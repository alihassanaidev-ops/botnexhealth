"""Every RLS-protected table, checked mechanically — no hand-written case per table.

The hand-written RLS suite covers 20 of the 38 baseline-protected tables, and
none of the tables added by the ~43 migrations since. Coverage written by hand,
one table at a time, always trails the newest features — which is precisely
where bugs live. The Test Suite shipped to staging with an RLS context that
matched no policy at all: every query returned zero rows, and 31 unit tests
passed throughout because they mocked the session.

So rather than 38 more bespoke tests, this asks the database structural
questions it can answer for *every* table at once, and fails when a new table
arrives without the protection its neighbours have. Coverage becomes opt-out.

It runs against the same fixture as the rest of the tier: real Postgres, the
real ``alembic upgrade head`` chain, and a non-superuser role so PostgreSQL
actually enforces the policies. What passes here is what a freshly built
production database does.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# Plumbing only — the container, the migration run and the non-superuser role.
# Reused rather than duplicated so this file cannot drift from the schema the
# rest of the tier tests against.
from tests.integration.test_rls_postgres import (
    _apply_rls_migration,
    _asyncpg_url,
    _create_app_role,
    _database_url_with_credentials,
)

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def swept_database_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    try:
        container = postgres_module.PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - depends on local Docker
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")
    try:
        yield _asyncpg_url(container.get_connection_url())
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="module")
async def rls_engine(swept_database_url: str):
    """A migrated database, connected as the unprivileged app role.

    Deliberately unseeded: these checks ask the catalog what the schema
    guarantees, which is true of an empty database and of a full one.
    """
    await _apply_rls_migration(swept_database_url)
    await _create_app_role(swept_database_url)
    engine = create_async_engine(
        _database_url_with_credentials(
            swept_database_url, username="rls_app", password="rls_app"
        ),
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()

#: Tables that legitimately hold no tenant data and so need no institution
#: scoping. Each needs a reason: this list is the only way to opt out, and an
#: unexplained entry is how a real gap gets parked here permanently.
NOT_TENANT_SCOPED: dict[str, str] = {
    "alembic_version": "migration bookkeeping",
    "institutions": "the tenant itself — scoped by id, not by institution_id",
    "institution_groups": "spans institutions by definition",
}


def _protected_tables() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "_baseline", ROOT / "alembic" / "versions" / "20260510_consolidated_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.PROTECTED_TABLES)


async def _tables_with_rls(conn) -> dict[str, dict]:
    """What the database actually enforces, per table.

    Read from the catalog rather than parsed out of migration source: policy
    SQL is assembled with interpolated constants, so static parsing produces
    both false positives and false negatives. The catalog is the truth.
    """
    rows = (
        await conn.execute(
            text(
                """
                SELECT c.relname                AS table_name,
                       c.relrowsecurity         AS rls_enabled,
                       c.relforcerowsecurity    AS rls_forced,
                       count(p.polname)         AS policy_count
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_policy p ON p.polrelid = c.oid
                WHERE n.nspname = 'public'
                  -- 'p' is a partitioned parent (audit_logs). Excluding it
                  -- reports the parent as missing; including its children
                  -- reports them as unprotected. Policies live on the parent
                  -- and apply to reads through it, which is how the app reads.
                  AND c.relkind IN ('r', 'p')
                  AND NOT c.relispartition
                GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                """
            )
        )
    ).all()
    return {
        r.table_name: {
            "enabled": r.rls_enabled,
            "forced": r.rls_forced,
            "policies": r.policy_count,
        }
        for r in rows
    }


async def _columns(conn, table: str) -> set[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table},
        )
    ).all()
    return {r.column_name for r in rows}


# ── Structural guarantees, every table ────────────────────────────────


async def test_every_protected_table_has_rls_enabled_and_forced(rls_engine):
    """Enabled is not enough — unforced RLS is bypassed by the table owner.

    A table the migrations list as protected but which Postgres is not
    enforcing on is a silent cross-tenant read.
    """
    async with rls_engine.begin() as conn:
        actual = await _tables_with_rls(conn)

    problems = []
    for table in sorted(_protected_tables()):
        info = actual.get(table)
        if info is None:
            problems.append(f"{table}: listed as protected but not in the database")
        elif not info["enabled"]:
            problems.append(f"{table}: RLS NOT ENABLED")
        elif not info["forced"]:
            problems.append(f"{table}: RLS enabled but NOT FORCED (owner bypasses it)")
    assert not problems, "RLS not enforced on:\n  " + "\n  ".join(problems)


async def test_no_protected_table_is_left_without_a_policy(rls_engine):
    """RLS on with zero policies denies everyone — including the app.

    This is the failure mode that reached staging in a different guise: not a
    leak, but a table nothing can read, which surfaces as an empty list rather
    than an error and is therefore easy to ship.
    """
    async with rls_engine.begin() as conn:
        actual = await _tables_with_rls(conn)

    starved = [
        t
        for t in sorted(_protected_tables())
        if actual.get(t, {}).get("enabled") and actual[t]["policies"] == 0
    ]
    assert not starved, (
        "RLS enabled with no policy — these are invisible to every context: "
        f"{starved}"
    )


async def test_every_tenant_table_in_the_database_is_protected(rls_engine):
    """The drift catch, pointed at the database rather than at the models.

    ``test_rls_protected_tables_coverage`` asks the same question of SQLAlchemy
    models. A table created by raw SQL in a migration has no model, so it is
    invisible to that check and visible to this one.
    """
    async with rls_engine.begin() as conn:
        actual = await _tables_with_rls(conn)
        unprotected = []
        for table, info in sorted(actual.items()):
            if table in NOT_TENANT_SCOPED or info["enabled"]:
                continue
            if "institution_id" in await _columns(conn, table):
                unprotected.append(table)

    assert not unprotected, (
        "these tables carry institution_id but have no RLS — any context reads "
        f"every tenant's rows: {unprotected}"
    )


async def test_the_opt_out_list_is_still_accurate(rls_engine):
    """Stops the waiver list quietly becoming where gaps go to hide."""
    async with rls_engine.begin() as conn:
        actual = await _tables_with_rls(conn)
        stale = [t for t in NOT_TENANT_SCOPED if t not in actual and t != "alembic_version"]
        wrongly_waived = []
        for table in NOT_TENANT_SCOPED:
            if table in actual and "institution_id" in await _columns(conn, table):
                if table != "institutions":
                    wrongly_waived.append(table)

    assert not stale, f"waived tables that no longer exist: {stale}"
    assert not wrongly_waived, (
        f"waived as not-tenant-scoped but they carry institution_id: {wrongly_waived}"
    )


# ── Contexts, not tables ──────────────────────────────────────────────


def _context_types_used_in_code() -> dict[str, list[str]]:
    """Every RLS context literal the application passes, and where.

    Parsed with ``ast`` rather than grepped: the calls wrap across lines and a
    regex either misses those or invents matches. Only literal strings are
    collected — a context built at runtime cannot be checked here and should
    not exist.
    """
    import ast

    found: dict[str, list[str]] = {}
    for path in (ROOT / "src" / "app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name not in {"get_system_db_session", "system"}:
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                found.setdefault(first.value, []).append(where)
    return found


async def _context_types_the_database_grants(conn) -> set[str]:
    """Context types that appear in a real policy expression.

    Read from ``pg_policy`` rather than the migration source, because policy
    SQL is assembled with interpolated constants — grepping the migrations
    reports contexts that do not exist and misses ones that do. (Confirmed the
    hard way: a source grep flagged four healthy contexts as missing.)
    """
    import re

    rows = (
        await conn.execute(
            text(
                """
                SELECT pg_get_expr(p.polqual, p.polrelid)      AS using_expr,
                       pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
                FROM pg_policy p
                """
            )
        )
    ).all()
    blob = " ".join(
        part for row in rows for part in (row.using_expr, row.check_expr) if part
    )

    granted: set[str] = set()
    # Two forms, and missing the second is a false-positive machine: a policy
    # written as `= ANY (ARRAY['retell', 'retell_function'])` grants both, but
    # a regex looking only for `= 'x'` reports them as ungranted. Both real
    # contexts were flagged that way before this handled the ARRAY form.
    granted.update(re.findall(r"app_rls_context_type\(\)\s*=\s*'([a-z_]+)'", blob))
    for array_body in re.findall(
        r"app_rls_context_type\(\)\s*=\s*ANY\s*\(\s*ARRAY\[(.*?)\]", blob, re.S
    ):
        granted.update(re.findall(r"'([a-z_]+)'", array_body))
    return granted


async def test_every_context_the_code_uses_is_one_the_database_grants(rls_engine):
    """The bug that reached staging, generalised.

    ``get_system_db_session("test_suite")`` set a context no policy mentions.
    Postgres does not reject an unknown context — it simply matches nothing, so
    every query returned zero rows. The endpoint answered ``200 {"count": 0}``
    with five locations in the table, and 31 unit tests passed because they all
    mocked the session.

    Nothing anywhere could have caught it: the mocked tier cannot see RLS at
    all, and the string matched no policy precisely because no policy existed.
    This closes that.
    """
    async with rls_engine.begin() as conn:
        granted = await _context_types_the_database_grants(conn)

    used = _context_types_used_in_code()
    ungranted = {
        ctx: sites for ctx, sites in used.items() if ctx not in granted
    }

    assert not ungranted, (
        "these RLS contexts are used in code but no policy grants them "
        "anything, so every query under them silently returns zero rows:\n  "
        + "\n  ".join(
            f"{ctx!r} at {', '.join(sites[:3])}" for ctx, sites in sorted(ungranted.items())
        )
    )


async def test_the_code_uses_at_least_the_contexts_we_expect(rls_engine):
    """Guards the parser itself.

    If the AST walk silently stopped matching — a helper renamed, a call
    reshaped — the check above would pass by finding nothing to check.
    """
    used = _context_types_used_in_code()
    assert len(used) >= 10, f"context parser found only {sorted(used)} — is it still matching?"
    assert "user" in used and "celery" in used
