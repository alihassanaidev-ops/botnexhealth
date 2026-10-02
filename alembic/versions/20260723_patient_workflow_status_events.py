"""Add patient workflow status event trail.

Revision ID: 20260723_patient_workflow_status_events
Revises: 20260722_gotracker_adapter_location_config
"""

from alembic import op

revision = "20260723_patient_workflow_status_events"
down_revision = "20260722_gotracker_adapter_location_config"
branch_labels = None
depends_on = None


def _status_events_rls_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN (
                'celery',
                'dead_letter',
                'nexhealth_webhooks',
                'gotracker_webhooks',
                'retell',
                'twilio'
            )
            AND patient_workflow_status_events.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND patient_workflow_status_events.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR patient_workflow_status_events.location_id = app_rls_location_id()
                OR patient_workflow_status_events.location_id IS NULL
            )
        )
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_workflow_status_events (
            id uuid PRIMARY KEY,
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            contact_id uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_id uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id uuid NOT NULL REFERENCES automation_workflow_versions(id) ON DELETE RESTRICT,
            workflow_run_id uuid NOT NULL REFERENCES automation_workflow_runs(id) ON DELETE CASCADE,
            step_id varchar(120),
            trigger_ref_type varchar(60),
            trigger_ref_id varchar(160),
            status varchar(80) NOT NULL,
            note text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_institution_id
        ON patient_workflow_status_events (institution_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_location_id
        ON patient_workflow_status_events (location_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_contact_id
        ON patient_workflow_status_events (contact_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_contact
        ON patient_workflow_status_events (institution_id, contact_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_run
        ON patient_workflow_status_events (workflow_run_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_workflow_status_events_status
        ON patient_workflow_status_events (institution_id, status, created_at)
        """
    )

    expr = _status_events_rls_expr()
    op.execute("ALTER TABLE patient_workflow_status_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE patient_workflow_status_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS patient_workflow_status_events_rls "
        "ON patient_workflow_status_events"
    )
    op.execute(
        "CREATE POLICY patient_workflow_status_events_rls "
        f"ON patient_workflow_status_events FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON patient_workflow_status_events TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS patient_workflow_status_events_rls ON patient_workflow_status_events")
    op.drop_table("patient_workflow_status_events")
