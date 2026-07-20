"""NexHealth sync-status working set.

Revision ID: 20260718_nexhealth_sync_status
Revises: 20260717_patient_working_set
"""

from __future__ import annotations

from alembic import op

revision = "20260718_nexhealth_sync_status"
down_revision = "20260717_patient_working_set"
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
        CREATE TABLE IF NOT EXISTS nexhealth_sync_statuses (
            id                         uuid PRIMARY KEY,
            institution_id             uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id                uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            subdomain                  varchar(160) NOT NULL,
            nexhealth_location_id      varchar(160) NOT NULL,
            sync_source_type           varchar(80),
            sync_source_name           varchar(160),
            emr_payload                jsonb,
            locations_payload          jsonb,
            read_status                varchar(32),
            read_status_at             timestamptz,
            write_status               varchar(32),
            write_status_at            timestamptz,
            last_event                 varchar(64),
            last_checked_at            timestamptz NOT NULL DEFAULT now(),
            created_at                 timestamptz NOT NULL DEFAULT now(),
            updated_at                 timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_nexhealth_sync_status_location
                UNIQUE (institution_id, location_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_sync_statuses_checked "
        "ON nexhealth_sync_statuses (institution_id, last_checked_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_sync_statuses_read "
        "ON nexhealth_sync_statuses (institution_id, read_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_sync_statuses_write "
        "ON nexhealth_sync_statuses (institution_id, write_status)"
    )
    _apply_rls("nexhealth_sync_statuses")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS nexhealth_sync_statuses_rls ON nexhealth_sync_statuses")
    op.execute("DROP TABLE IF EXISTS nexhealth_sync_statuses")
