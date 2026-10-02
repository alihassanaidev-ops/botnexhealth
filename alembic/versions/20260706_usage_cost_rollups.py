"""Daily usage & cost rollup table (Plan 11 M-2).

Pre-aggregates ``usage_events`` into one row per
(institution_id, location_id, usage_date, channel, direction) so reporting reads
are small SUMs. Written by the admin-role recompute script (bypasses RLS);
read by the reporting API under the ``user`` context. Same institution/location
RLS policy as usage_events.

Revision ID: 20260706_usage_cost_rollups
Revises: 20260706_usage_event_tags
"""

from __future__ import annotations

from alembic import op

revision = "20260706_usage_cost_rollups"
down_revision = "20260706_usage_event_tags"
branch_labels = None
depends_on = None

TABLE = "usage_cost_rollups"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_cost_rollups (
            institution_id     uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id        uuid NOT NULL,
            usage_date         date NOT NULL,
            channel            varchar(20) NOT NULL,
            direction          varchar(20) NOT NULL,
            event_count        bigint NOT NULL DEFAULT 0,
            total_segments     bigint NOT NULL DEFAULT 0,
            total_dials        bigint NOT NULL DEFAULT 0,
            total_emails       bigint NOT NULL DEFAULT 0,
            total_minutes      numeric(16, 4) NOT NULL DEFAULT 0,
            total_cost_amount  numeric(16, 5) NOT NULL DEFAULT 0,
            currency           varchar(3) NOT NULL DEFAULT 'USD',
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_usage_cost_rollups
                PRIMARY KEY (institution_id, location_id, usage_date, channel, direction)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_usage_cost_rollups_institution_date "
        "ON usage_cost_rollups (institution_id, usage_date)"
    )

    expr = _rls_expr(TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL
        USING ({expr})
        WITH CHECK ({expr})
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON usage_cost_rollups TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute("DROP TABLE IF EXISTS usage_cost_rollups")
