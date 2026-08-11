"""Track pending GoTracker appointment writebacks.

Revision ID: 20260811_gotracker_appt_writebacks
Revises: 20260804_gotracker_status_snapshot
"""

from __future__ import annotations

from alembic import op

revision = "20260811_gotracker_appt_writebacks"
down_revision = "20260804_gotracker_status_snapshot"
branch_labels = None
depends_on = None


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'gotracker_webhooks')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def _apply_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
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
        CREATE TABLE IF NOT EXISTS gotracker_appointment_writebacks (
            id                    uuid PRIMARY KEY,
            institution_id        uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id           uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            appointment_id        varchar(160) NOT NULL,
            contact_id            uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_run_id       uuid REFERENCES automation_workflow_runs(id) ON DELETE SET NULL,
            step_id               varchar(120),
            action                varchar(32) NOT NULL,
            status                varchar(32) NOT NULL DEFAULT 'pending',
            previous_start_time   timestamptz,
            requested_start_time  timestamptz,
            provider_id           varchar(160),
            status_id             integer,
            confirmed             boolean,
            preconfirmed          boolean,
            completed_event_id    varchar(160),
            failed_event_id       varchar(160),
            error_message         text,
            completed_at          timestamptz,
            failed_at             timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_gotracker_appt_writebacks_action
                CHECK (action IN ('reschedule', 'cancel', 'confirm', 'status')),
            CONSTRAINT ck_gotracker_appt_writebacks_status
                CHECK (status IN ('pending', 'completed', 'failed'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gotracker_appt_writebacks_pending
        ON gotracker_appointment_writebacks (institution_id, appointment_id, status, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gotracker_appt_writebacks_run
        ON gotracker_appointment_writebacks (workflow_run_id)
        """
    )
    _apply_rls("gotracker_appointment_writebacks")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS gotracker_appointment_writebacks_rls "
        "ON gotracker_appointment_writebacks"
    )
    op.execute("DROP TABLE IF EXISTS gotracker_appointment_writebacks")
