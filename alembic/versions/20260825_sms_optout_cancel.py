"""Allow tenant-scoped Twilio STOP workflow cancellation.

Revision ID: 20260825_sms_optout_cancel
Revises: 20260825_sms_suppress_location
"""

from __future__ import annotations

from alembic import op


revision = "20260825_sms_optout_cancel"
down_revision = "20260825_sms_suppress_location"
branch_labels = None
depends_on = None


def _twilio_scope(table: str) -> str:
    return f"""
        app_rls_context_type() = 'twilio'
        AND {table}.institution_id = app_rls_institution_id()
        AND {table}.location_id = app_rls_location_id()
    """


def upgrade() -> None:
    run_scope = _twilio_scope("automation_workflow_runs")
    timer_scope = _twilio_scope("automation_workflow_timers")
    event_scope = (
        _twilio_scope("automation_workflow_events")
        + " AND automation_workflow_events.event_type = 'run.cancelled'"
    )

    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_runs_twilio_update "
        "ON automation_workflow_runs"
    )
    op.execute(
        "CREATE POLICY automation_workflow_runs_twilio_update "
        "ON automation_workflow_runs FOR UPDATE "
        f"USING ({run_scope}) WITH CHECK ({run_scope})"
    )

    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_timers_twilio_select "
        "ON automation_workflow_timers"
    )
    op.execute(
        "CREATE POLICY automation_workflow_timers_twilio_select "
        "ON automation_workflow_timers FOR SELECT "
        f"USING ({timer_scope})"
    )
    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_timers_twilio_update "
        "ON automation_workflow_timers"
    )
    op.execute(
        "CREATE POLICY automation_workflow_timers_twilio_update "
        "ON automation_workflow_timers FOR UPDATE "
        f"USING ({timer_scope}) WITH CHECK ({timer_scope})"
    )

    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_events_twilio_insert "
        "ON automation_workflow_events"
    )
    op.execute(
        "CREATE POLICY automation_workflow_events_twilio_insert "
        "ON automation_workflow_events FOR INSERT "
        f"WITH CHECK ({event_scope})"
    )
    # SQLAlchemy retrieves server-generated event defaults with INSERT ...
    # RETURNING, which PostgreSQL subjects to SELECT row security as well.
    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_events_twilio_select_cancelled "
        "ON automation_workflow_events"
    )
    op.execute(
        "CREATE POLICY automation_workflow_events_twilio_select_cancelled "
        "ON automation_workflow_events FOR SELECT "
        f"USING ({event_scope})"
    )


def downgrade() -> None:
    for table, policy in (
        (
            "automation_workflow_events",
            "automation_workflow_events_twilio_select_cancelled",
        ),
        ("automation_workflow_events", "automation_workflow_events_twilio_insert"),
        ("automation_workflow_timers", "automation_workflow_timers_twilio_update"),
        ("automation_workflow_timers", "automation_workflow_timers_twilio_select"),
        ("automation_workflow_runs", "automation_workflow_runs_twilio_update"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
