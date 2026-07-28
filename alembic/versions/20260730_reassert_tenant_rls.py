"""Re-assert RLS on tenant-scoped event/voice tables.

Revision ID: 20260730_reassert_tenant_rls
Revises: 20260729_outbound_voice_named_profiles
"""

from __future__ import annotations

from alembic import op

revision = "20260730_reassert_tenant_rls"
down_revision = "20260729_outbound_voice_named_profiles"
branch_labels = None
depends_on = None


def _usage_rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
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


def _voice_rls_expr(table: str) -> str:
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


def _apply_rls(table: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_rls ON {table} FOR ALL
        USING ({expr})
        WITH CHECK ({expr})
        """
    )


def upgrade() -> None:
    _apply_rls("usage_events", _usage_rls_expr("usage_events"))
    for table in ("outbound_voice_profiles", "workflow_voice_attempts"):
        _apply_rls(table, _voice_rls_expr(table))

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    usage_events,
                    outbound_voice_profiles,
                    workflow_voice_attempts
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in ("usage_events", "outbound_voice_profiles", "workflow_voice_attempts"):
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
