"""Harden location-admin campaign scope and protect workflow schedules.

Revision ID: 20260908_loc_campaign_rls
Revises: 20260907_legacy_triggers
Create Date: 2026-09-08

Location admins may author campaigns, but only for the clinic assigned to their
account. Existing automation policies also admitted ``location_id IS NULL``
rows to location-scoped user sessions. A restrictive policy closes that gap
without changing the permissive policies used by institution admins, Celery,
webhooks, or other service contexts.
"""

from __future__ import annotations

from alembic import op


revision = "20260908_loc_campaign_rls"
down_revision = "20260907_legacy_triggers"
branch_labels = None
depends_on = None


LOCATION_GUARDED_TABLES = (
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_runs",
    "automation_workflow_step_executions",
    "automation_workflow_drip_states",
    "automation_workflow_split_assignments",
    "automation_workflow_timers",
    "automation_workflow_events",
    "workflow_voice_attempts",
    "campaign_audience_definitions",
    "campaign_audience_previews",
    "campaign_response_events",
    "campaign_staff_handoffs",
    "campaign_conversation_threads",
    "campaign_metrics_daily",
    "campaign_split_metrics_daily",
    "patient_workflow_status_events",
    "outbound_email_messages",
    "quiet_hours_exceptions",
)


def _location_admin_guard(table: str) -> str:
    return f"""
        app_rls_context_type() <> 'user'
        OR app_rls_role() <> 'LOCATION_ADMIN'
        OR (
            app_rls_location_id() IS NOT NULL
            AND {table}.location_id = app_rls_location_id()
        )
    """


def _workflow_schedule_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND workflow_schedules.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR workflow_schedules.location_id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND workflow_schedules.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR (
                    app_rls_role() = 'LOCATION_ADMIN'
                    AND app_rls_location_id() IS NOT NULL
                    AND workflow_schedules.location_id = app_rls_location_id()
                )
            )
        )
    """


def upgrade() -> None:
    for table in LOCATION_GUARDED_TABLES:
        guard = _location_admin_guard(table)
        op.execute(
            f"DROP POLICY IF EXISTS {table}_location_admin_guard ON {table}"
        )
        op.execute(
            f"""
            CREATE POLICY {table}_location_admin_guard
            ON {table} AS RESTRICTIVE FOR ALL
            USING ({guard})
            WITH CHECK ({guard})
            """
        )

    schedule_expr = _workflow_schedule_expr()
    op.execute("ALTER TABLE workflow_schedules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_schedules FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS workflow_schedules_rls ON workflow_schedules"
    )
    op.execute(
        f"""
        CREATE POLICY workflow_schedules_rls ON workflow_schedules FOR ALL
        USING ({schedule_expr})
        WITH CHECK ({schedule_expr})
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_schedules
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS workflow_schedules_rls ON workflow_schedules"
    )
    op.execute("ALTER TABLE workflow_schedules NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_schedules DISABLE ROW LEVEL SECURITY")
    for table in LOCATION_GUARDED_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table}_location_admin_guard ON {table}"
        )
