"""Add workflow Drip action state.

Revision ID: 20260731_workflow_drip_action
Revises: 20260730_reassert_tenant_rls
"""

from __future__ import annotations

from alembic import op

revision = "20260731_workflow_drip_action"
down_revision = "20260730_reassert_tenant_rls"
branch_labels = None
depends_on = None


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


def _apply_rls(table: str) -> None:
    expr = _rls_expr(table)
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
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_drip_states (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id        uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id           uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id           uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id   uuid NOT NULL REFERENCES automation_workflow_versions(id) ON DELETE CASCADE,
            step_id               varchar(120) NOT NULL,
            batch_size            integer NOT NULL,
            interval_seconds      integer NOT NULL,
            current_batch_number  integer NOT NULL DEFAULT 0,
            current_batch_count   integer NOT NULL DEFAULT 0,
            next_due_at           timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_automation_drip_state_version_step
                UNIQUE (workflow_version_id, step_id),
            CONSTRAINT ck_automation_drip_state_batch_size CHECK (batch_size > 0),
            CONSTRAINT ck_automation_drip_state_batch_count CHECK (current_batch_count >= 0),
            CONSTRAINT ck_automation_drip_state_batch_number CHECK (current_batch_number >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_drip_states_institution "
        "ON automation_workflow_drip_states (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_drip_states_workflow_version "
        "ON automation_workflow_drip_states (workflow_version_id)"
    )
    _apply_rls("automation_workflow_drip_states")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON automation_workflow_drip_states
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS automation_workflow_drip_states_rls ON automation_workflow_drip_states")
    op.execute("DROP TABLE IF EXISTS automation_workflow_drip_states")
