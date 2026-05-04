"""Create call_metrics_daily rollup table.

Pre-aggregated daily call metrics for fast dashboard reads. Live
aggregate scans on ``calls`` are fine until the table grows past
~100k rows per institution; this rollup keeps dashboard p95 flat
as the underlying ``calls`` table grows. See
``src/app/services/dashboard_rollup.py`` for the recompute logic.

RLS policy mirrors ``calls`` exactly — same context types, same
scope checks. The rollup row is not PHI itself but it derives
from PHI rows; tenant isolation MUST hold here too.

Revision ID: 20260513_metrics
Revises: 20260512_drop_dead
"""

from __future__ import annotations

from alembic import op


revision = "20260513_metrics"
down_revision = "20260512_drop_dead"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS call_metrics_daily (
            institution_id          uuid    NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid    NOT NULL,
            call_date               date    NOT NULL,
            total_calls             integer NOT NULL DEFAULT 0,
            new_patient_calls       integer NOT NULL DEFAULT 0,
            complaint_calls         integer NOT NULL DEFAULT 0,
            insurance_billing_calls integer NOT NULL DEFAULT 0,
            total_duration_seconds  bigint  NOT NULL DEFAULT 0,
            tag_counts              jsonb   NOT NULL DEFAULT '{}'::jsonb,
            updated_at              timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (institution_id, location_id, call_date)
        )
        """
    )

    # Reverse-direction lookup index for "all-time totals for a location",
    # which is what the per-location aggregate dashboard does. The PK
    # already covers (institution, location, date); this covers the case
    # where the planner wants to seek by location first (rare but cheap
    # to support).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_metrics_daily_location_date "
        "ON call_metrics_daily (location_id, call_date)"
    )

    # RLS — mirrors the calls policy. Reuses the same SECURITY DEFINER
    # helpers (app_rls_is_super_admin, app_rls_institution_id,
    # app_rls_location_id) defined in the consolidated baseline.
    op.execute("ALTER TABLE call_metrics_daily ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE call_metrics_daily FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS call_metrics_daily_rls ON call_metrics_daily")
    op.execute(
        """
        CREATE POLICY call_metrics_daily_rls ON call_metrics_daily FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter', 'audit')
                AND call_metrics_daily.institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND call_metrics_daily.institution_id = app_rls_institution_id()
                AND (
                    app_rls_location_id() IS NULL
                    OR call_metrics_daily.location_id = app_rls_location_id()
                    OR call_metrics_daily.location_id =
                        '00000000-0000-0000-0000-000000000000'::uuid
                )
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('celery', 'audit')
                AND call_metrics_daily.institution_id = app_rls_institution_id()
            )
        )
        """
    )

    # Grants for the runtime app role (provisioned by migrate_database.py).
    # The role exists once the migration task has run on this DB; on a
    # truly-fresh DB the GRANT is a no-op (role doesn't exist yet) so we
    # use a DO block to swallow that case.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON call_metrics_daily TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS call_metrics_daily")
