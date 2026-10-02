"""Add campaign audience definitions and previews.

Revision ID: 20260716_campaign_audience
Revises: 20260715_campaign_analytics
"""

from __future__ import annotations

from alembic import op

revision = "20260716_campaign_audience"
down_revision = "20260715_campaign_analytics"
branch_labels = None
depends_on = None

TABLES = ("campaign_audience_definitions", "campaign_audience_previews")


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


def _enable_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )


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
        ALTER TABLE appointment_working_set
            ADD COLUMN IF NOT EXISTS provider_id varchar(160),
            ADD COLUMN IF NOT EXISTS appointment_type_id varchar(160)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_provider_id "
        "ON appointment_working_set (provider_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_appointment_type_id "
        "ON appointment_working_set (appointment_type_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_audience_definitions (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id         uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            segment             jsonb NOT NULL DEFAULT '{}'::jsonb,
            exclusions          jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_audience_definitions_workflow UNIQUE (workflow_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_audience_definitions_institution "
        "ON campaign_audience_definitions (institution_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_audience_previews (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id          uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id  uuid REFERENCES automation_workflow_versions(id) ON DELETE SET NULL,
            segment              jsonb NOT NULL DEFAULT '{}'::jsonb,
            exclusions           jsonb NOT NULL DEFAULT '{}'::jsonb,
            counts_by_reason     jsonb NOT NULL DEFAULT '{}'::jsonb,
            included_count       integer NOT NULL DEFAULT 0,
            excluded_count       integer NOT NULL DEFAULT 0,
            created_by_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
            expires_at           timestamptz NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_audience_previews_workflow_created "
        "ON campaign_audience_previews (workflow_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_audience_previews_expires "
        "ON campaign_audience_previews (expires_at)"
    )

    for table in TABLES:
        _enable_rls(table)
        _grant(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute("DROP TABLE IF EXISTS campaign_audience_previews")
    op.execute("DROP TABLE IF EXISTS campaign_audience_definitions")
    op.execute("DROP INDEX IF EXISTS ix_appointment_working_set_appointment_type_id")
    op.execute("DROP INDEX IF EXISTS ix_appointment_working_set_provider_id")
    op.execute(
        """
        ALTER TABLE appointment_working_set
            DROP COLUMN IF EXISTS appointment_type_id,
            DROP COLUMN IF EXISTS provider_id
        """
    )
