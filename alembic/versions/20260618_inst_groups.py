"""Institution groups (DSO oversight) + GROUP_ADMIN role.

Adds:
- institution_groups table (RLS: super-admin manages; a GROUP_ADMIN reads its own group).
- institutions.group_id + users.group_id (nullable FKs, SET NULL on group delete).
- app_rls_group_id() helper + app.group_id GUC.
- Extends institutions + call_metrics_daily RLS so a GROUP_ADMIN can READ (not write)
  its group's institutions and their daily rollup. Reads stay single-institution-per-
  request (the API sets app.institution_id per member in a loop); these policies just
  authorize that read for the group role.

Revision ID: 20260618_inst_groups
Revises: 20260617_no_pms_mode
"""

from __future__ import annotations

from alembic import op


revision = "20260618_inst_groups"
down_revision = "20260617_no_pms_mode"
branch_labels = None
depends_on = None


# Verbatim copy of the baseline institutions USING/CHECK expression. Kept inline
# so this migration is self-contained; the only addition is the GROUP_ADMIN read clause.
_INSTITUTIONS_BASE = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
        AND institutions.id = app_rls_institution_id()
    )
    OR (
        app_rls_context_type() = 'audit'
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

_INSTITUTIONS_GROUP_READ = """
    OR (
        app_rls_context_type() = 'user'
        AND app_rls_role() = 'GROUP_ADMIN'
        AND institutions.group_id = app_rls_group_id()
    )
"""


def upgrade() -> None:
    # --- institution_groups table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS institution_groups (
            id          uuid PRIMARY KEY,
            name        varchar(255) NOT NULL,
            slug        varchar(100) NOT NULL UNIQUE,
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # --- group_id FKs on institutions + users ---
    op.execute(
        "ALTER TABLE institutions ADD COLUMN IF NOT EXISTS group_id uuid "
        "REFERENCES institution_groups(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_institutions_group_id ON institutions (group_id)")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id uuid "
        "REFERENCES institution_groups(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_group_id ON users (group_id)")

    # Widen the users role CHECK constraint to allow GROUP_ADMIN.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK ("
        "role IN ('SUPER_ADMIN','INSTITUTION_ADMIN','LOCATION_ADMIN','STAFF','GROUP_ADMIN'))"
    )

    # --- RLS helper for the new GUC ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_group_id()
        RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT app_rls_uuid('app.group_id')
        $$
        """
    )

    # --- institution_groups RLS ---
    op.execute("ALTER TABLE institution_groups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE institution_groups FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS institution_groups_rls ON institution_groups")
    op.execute(
        """
        CREATE POLICY institution_groups_rls ON institution_groups FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() = 'user'
                AND app_rls_role() = 'GROUP_ADMIN'
                AND institution_groups.id = app_rls_group_id()
            )
        )
        WITH CHECK (app_rls_is_super_admin())
        """
    )

    # --- extend institutions policy: GROUP_ADMIN read-only over its group ---
    op.execute("DROP POLICY IF EXISTS institutions_rls ON institutions")
    op.execute(
        f"""
        CREATE POLICY institutions_rls ON institutions FOR ALL
        USING ({_INSTITUTIONS_BASE} {_INSTITUTIONS_GROUP_READ})
        WITH CHECK ({_INSTITUTIONS_BASE})
        """
    )

    # --- extend call_metrics_daily policy: GROUP_ADMIN read of a member's rollup ---
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
            OR (
                app_rls_context_type() = 'user'
                AND app_rls_role() = 'GROUP_ADMIN'
                AND call_metrics_daily.institution_id = app_rls_institution_id()
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

    # --- grant the runtime app role access to the new table (no-op on fresh DB) ---
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON institution_groups TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # Restore the pre-group call_metrics_daily policy.
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

    # Restore the pre-group institutions policy.
    op.execute("DROP POLICY IF EXISTS institutions_rls ON institutions")
    op.execute(
        f"""
        CREATE POLICY institutions_rls ON institutions FOR ALL
        USING ({_INSTITUTIONS_BASE})
        WITH CHECK ({_INSTITUTIONS_BASE})
        """
    )

    op.execute("DROP POLICY IF EXISTS institution_groups_rls ON institution_groups")
    op.execute("DROP FUNCTION IF EXISTS app_rls_group_id()")

    # Restore the original (pre-GROUP_ADMIN) users role CHECK.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK ("
        "role IN ('SUPER_ADMIN','INSTITUTION_ADMIN','LOCATION_ADMIN','STAFF'))"
    )

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS group_id")
    op.execute("ALTER TABLE institutions DROP COLUMN IF EXISTS group_id")
    op.execute("DROP TABLE IF EXISTS institution_groups")
