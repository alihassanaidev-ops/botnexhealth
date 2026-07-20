"""Patient working set and patient webhook ledger fields.

Revision ID: 20260717_patient_working_set
Revises: 20260716_campaign_audience
"""

from __future__ import annotations

from alembic import op

revision = "20260717_patient_working_set"
down_revision = "20260716_campaign_audience"
branch_labels = None
depends_on = None


def _rls_expr(table: str) -> str:
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
        CREATE TABLE IF NOT EXISTS patient_working_set (
            id                       uuid PRIMARY KEY,
            institution_id           uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            primary_location_id      uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            contact_id               uuid REFERENCES contacts(id) ON DELETE SET NULL,
            nexhealth_patient_id     varchar(160) NOT NULL,
            nexhealth_location_ids   jsonb NOT NULL DEFAULT '[]'::jsonb,
            first_name               varchar(100),
            last_name                varchar(100),
            full_name                varchar(200),
            preferred_language       varchar(32),
            inactive                 boolean NOT NULL DEFAULT false,
            unsubscribe_sms          boolean NOT NULL DEFAULT false,
            is_new_patient           boolean NOT NULL DEFAULT false,
            last_event               varchar(64),
            last_synced_at           timestamptz NOT NULL DEFAULT now(),
            created_at               timestamptz NOT NULL DEFAULT now(),
            updated_at               timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_patient_working_set_patient
                UNIQUE (institution_id, nexhealth_patient_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_working_set_synced "
        "ON patient_working_set (institution_id, last_synced_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_working_set_contact "
        "ON patient_working_set (contact_id)"
    )
    _apply_rls("patient_working_set")

    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS nexhealth_patient_id varchar(160)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_patient "
        "ON nexhealth_webhook_events (nexhealth_patient_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_nexhealth_webhook_events_patient")
    op.execute("ALTER TABLE nexhealth_webhook_events DROP COLUMN IF EXISTS nexhealth_patient_id")
    op.execute("DROP POLICY IF EXISTS patient_working_set_rls ON patient_working_set")
    op.execute("DROP TABLE IF EXISTS patient_working_set")
