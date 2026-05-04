"""Narrow institutions policy for retell_lookup / twilio_lookup contexts.

Revision ID: 20260508_narrow_inst_lookup
Revises: 20260507_location_slug_per_inst
Create Date: 2026-05-08

The original institutions policy in 20260506_rls_full_staged allowed ANY
query under `retell_lookup` or `twilio_lookup` to see every institutions
row globally:

    OR app_rls_context_type() IN ('retell_lookup', 'twilio_lookup')

These contexts are used in webhook handlers BEFORE tenant resolution, so
the rows ARE eventually scoped via a join to institution_locations. But
"the application code joins correctly" is exactly the trust boundary
that FORCE RLS exists to harden. A future query in those contexts that
did ``SELECT * FROM institutions`` (or that an attacker reached via SQL
injection inside the same context) would exfiltrate every institution.

The naive narrow expression — EXISTS against institution_locations —
deadlocks because institution_locations' own policy contains EXISTS
against institutions (for `middleware_lookup`), so Postgres detects an
infinite recursion in row-security policies.

Fix: small SECURITY DEFINER lookup helpers that resolve
agent_id / twilio_number → institution_id while bypassing RLS. The
helpers run as the function owner (the role applying the migration)
which has BYPASSRLS implicitly via SECURITY DEFINER, so they don't
re-trigger any policy. The policies then check
``id = helper(external_id)`` which is a direct equality, not a
subquery, and there's no cycle.

The helpers are intentionally narrow: they take ONE column value and
return ONE column value, both already known to the caller (the GUC
they set). They cannot be used to enumerate rows the caller
shouldn't see.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260508_narrow_inst_lookup"
down_revision: Union[str, None] = "20260507_location_slug_per_inst"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_INSTITUTIONS_EXPR = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
        AND institutions.id = app_rls_institution_id()
    )
    OR (
        app_rls_context_type() = 'middleware_lookup'
        AND institutions.slug = app_rls_external_id()
    )
    OR (
        app_rls_context_type() = 'retell_lookup'
        AND institutions.id = app_rls_inst_for_retell_agent(app_rls_external_id())
    )
    OR (
        app_rls_context_type() = 'twilio_lookup'
        AND institutions.id = app_rls_inst_for_twilio_number(app_rls_external_id())
    )
    OR (
        app_rls_context_type() = 'user'
        AND institutions.id = app_rls_institution_id()
    )
"""


_OLD_INSTITUTIONS_EXPR = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
        AND institutions.id = app_rls_institution_id()
    )
    OR (
        app_rls_context_type() = 'middleware_lookup'
        AND institutions.slug = app_rls_external_id()
    )
    OR app_rls_context_type() IN ('retell_lookup', 'twilio_lookup')
    OR (
        app_rls_context_type() = 'user'
        AND institutions.id = app_rls_institution_id()
    )
"""


def upgrade() -> None:
    # SECURITY DEFINER lookup helpers. STABLE so the planner can cache
    # within a statement. Returns NULL for missing/empty input.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_inst_for_retell_agent(agent text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT institution_id FROM institution_locations
            WHERE retell_agent_id = agent
              AND agent IS NOT NULL AND agent <> ''
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_inst_for_twilio_number(num text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT institution_id FROM institution_locations
            WHERE twilio_from_number = num
              AND num IS NOT NULL AND num <> ''
            LIMIT 1
        $$;
        """
    )

    op.execute("DROP POLICY IF EXISTS institutions_rls ON institutions;")
    op.execute(
        f"""
        CREATE POLICY institutions_rls ON institutions
        FOR ALL
        USING ({_NEW_INSTITUTIONS_EXPR})
        WITH CHECK ({_NEW_INSTITUTIONS_EXPR});
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS institutions_rls ON institutions;")
    op.execute(
        f"""
        CREATE POLICY institutions_rls ON institutions
        FOR ALL
        USING ({_OLD_INSTITUTIONS_EXPR})
        WITH CHECK ({_OLD_INSTITUTIONS_EXPR});
        """
    )
    op.execute("DROP FUNCTION IF EXISTS app_rls_inst_for_twilio_number(text);")
    op.execute("DROP FUNCTION IF EXISTS app_rls_inst_for_retell_agent(text);")
