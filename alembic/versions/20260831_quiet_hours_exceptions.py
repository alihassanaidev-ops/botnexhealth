"""Quiet-hours exceptions: per date, per patient, per message class.

Revision ID: 20260831_quiet_hours_exceptions
Revises: 20260831_outbound_call_limit

Item 20. One table serves all three exception kinds, because they are the same
question asked with different amounts of context; NULL in a targeting column
means "applies regardless" rather than "no match".

The rows describe when a named patient may be contacted, which is information
about that patient, so the table gets the same FORCE RLS treatment as every
other tenant-scoped table: reachable exactly within its own institution, and
within its own location when the session is location-scoped.

DDL is guarded with IF NOT EXISTS because the consolidated baseline builds the
whole schema from the models via ``create_all`` — on a fresh database this
table already exists by the time this migration runs.
"""

from __future__ import annotations

from alembic import op

revision = "20260831_quiet_hours_exceptions"
down_revision = "20260831_outbound_call_limit"
branch_labels = None
depends_on = None

TABLE = "quiet_hours_exceptions"
#: Spelled out rather than interpolated so the policy name is greppable —
#: tests/unit/test_rls_protected_tables_coverage.py scans migration source text
#: for "<table>_rls" to prove a post-baseline table is protected.
POLICY = "quiet_hours_exceptions_rls"


def _rls_expr(table: str) -> str:
    return f"""
        {table}.institution_id = app_rls_institution_id()
        AND (
            app_rls_location_id() IS NULL
            OR {table}.location_id = app_rls_location_id()
        )
    """


def _grant(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quiet_hours_exceptions (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id     uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            contact_id      uuid REFERENCES contacts(id) ON DELETE CASCADE,
            exception_date  date,
            content_class   varchar(40),
            is_blocked      boolean NOT NULL DEFAULT false,
            open_time       time,
            close_time      time,
            reason          text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quiet_hours_exceptions_location_date "
        f"ON {TABLE} (location_id, exception_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quiet_hours_exceptions_location_contact "
        f"ON {TABLE} (location_id, contact_id)"
    )

    expr = _rls_expr(TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    _grant(TABLE)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
