"""Appointment working-set projection + NexHealth event ledger (Plan 09 D-1/D-3/D-4).

- appointment_working_set: last-seen scheduling state per NexHealth appointment,
  used to detect reschedules (D-1) and serve the revalidation freshness window (D-2).
- nexhealth_webhook_events: event-level idempotency ledger (D-4).
- ix on automation_workflow_runs(trigger_ref_type, trigger_ref_id) so the webhook's
  cancel/reschedule run lookup is indexed (D-4 perf).

RLS: both new tables are written by the webhook route (context 'nexhealth_webhooks')
AND by Celery jobs (context 'celery'), and read by reporting/user context. The policy
context list MUST include all three or webhook writes silently fail RLS.

Revision ID: 20260707_appt_working_set
Revises: 20260706_usage_cost_rollups, 20260707_consent_basis

Merge migration: the Plan-11 usage chain (…→20260706_usage_cost_rollups) and the
Plan-12 consent_basis migration both branched off 20260706_dnc_scope, leaving two
heads. This revision merges them into a single head while adding the Plan-09 tables.
"""

from __future__ import annotations

from alembic import op

revision = "20260707_appt_working_set"
down_revision = ("20260706_usage_cost_rollups", "20260707_consent_basis")
branch_labels = None
depends_on = None


def _rls_expr(table: str) -> str:
    # 'nexhealth_webhooks' is the webhook route's session context — REQUIRED here.
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'nexhealth_webhooks', 'nexhealth_lookup')
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
        CREATE TABLE IF NOT EXISTS appointment_working_set (
            id                        uuid PRIMARY KEY,
            institution_id            uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id               uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            nexhealth_appointment_id  varchar(160) NOT NULL,
            nexhealth_patient_id      varchar(160),
            contact_id                uuid REFERENCES contacts(id) ON DELETE SET NULL,
            start_time                timestamptz,
            status                    varchar(20) NOT NULL DEFAULT 'scheduled',
            last_event                varchar(64),
            last_synced_at            timestamptz NOT NULL DEFAULT now(),
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_appointment_working_set_appt
                UNIQUE (institution_id, nexhealth_appointment_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_institution "
        "ON appointment_working_set (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_synced "
        "ON appointment_working_set (institution_id, last_synced_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS nexhealth_webhook_events (
            id                        uuid PRIMARY KEY,
            institution_id            uuid NOT NULL,
            nexhealth_appointment_id  varchar(160),
            event_type                varchar(64) NOT NULL,
            dedup_key                 varchar(300) NOT NULL,
            status                    varchar(32) NOT NULL DEFAULT 'PROCESSING',
            attempts                  integer NOT NULL DEFAULT 1,
            last_error                text,
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_nexhealth_webhook_events_dedup
                UNIQUE (institution_id, dedup_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_institution "
        "ON nexhealth_webhook_events (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_appt "
        "ON nexhealth_webhook_events (nexhealth_appointment_id)"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_trigger_ref "
        "ON automation_workflow_runs (trigger_ref_type, trigger_ref_id)"
    )

    _apply_rls("appointment_working_set")
    _apply_rls("nexhealth_webhook_events")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_automation_workflow_runs_trigger_ref")
    op.execute("DROP POLICY IF EXISTS nexhealth_webhook_events_rls ON nexhealth_webhook_events")
    op.execute("DROP TABLE IF EXISTS nexhealth_webhook_events")
    op.execute("DROP POLICY IF EXISTS appointment_working_set_rls ON appointment_working_set")
    op.execute("DROP TABLE IF EXISTS appointment_working_set")
