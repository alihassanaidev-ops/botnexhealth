"""Add campaign outcome analytics rollups.

Revision ID: 20260715_campaign_analytics
Revises: 20260714_campaign_responses
"""

from __future__ import annotations

from alembic import op

revision = "20260715_campaign_analytics"
down_revision = "20260714_campaign_responses"
branch_labels = None
depends_on = None

METRICS_TABLE = "campaign_metrics_daily"
DEFINITIONS_TABLE = "campaign_outcome_definitions"


def _metrics_rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


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
        CREATE TABLE IF NOT EXISTS campaign_metrics_daily (
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid NOT NULL,
            workflow_id             uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id     uuid NOT NULL REFERENCES automation_workflow_versions(id) ON DELETE CASCADE,
            metric_date             date NOT NULL,

            enrollments             bigint NOT NULL DEFAULT 0,
            active                  bigint NOT NULL DEFAULT 0,
            completed               bigint NOT NULL DEFAULT 0,
            failed                  bigint NOT NULL DEFAULT 0,
            cancelled               bigint NOT NULL DEFAULT 0,
            suppressed              bigint NOT NULL DEFAULT 0,

            sms_sent                bigint NOT NULL DEFAULT 0,
            sms_delivered           bigint NOT NULL DEFAULT 0,
            sms_failed              bigint NOT NULL DEFAULT 0,
            sms_replied             bigint NOT NULL DEFAULT 0,

            voice_attempted         bigint NOT NULL DEFAULT 0,
            voice_answered          bigint NOT NULL DEFAULT 0,
            voice_voicemail         bigint NOT NULL DEFAULT 0,
            voice_failed            bigint NOT NULL DEFAULT 0,

            email_sent              bigint NOT NULL DEFAULT 0,
            email_delivered         bigint NOT NULL DEFAULT 0,
            email_opened            bigint NOT NULL DEFAULT 0,
            email_clicked           bigint NOT NULL DEFAULT 0,
            email_bounced           bigint NOT NULL DEFAULT 0,

            confirmed               bigint NOT NULL DEFAULT 0,
            booked                  bigint NOT NULL DEFAULT 0,
            reschedule_requested    bigint NOT NULL DEFAULT 0,
            callback_requested      bigint NOT NULL DEFAULT 0,
            staff_handoff           bigint NOT NULL DEFAULT 0,
            opt_out                 bigint NOT NULL DEFAULT 0,

            total_cost              numeric(16, 5) NOT NULL DEFAULT 0,
            cost_per_booking        numeric(16, 5),
            cost_per_confirmation   numeric(16, 5),
            currency                varchar(3) NOT NULL DEFAULT 'USD',
            updated_at              timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_campaign_metrics_daily
                PRIMARY KEY (institution_id, location_id, workflow_id, workflow_version_id, metric_date)
        )
        """
    )
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_campaign_metrics_daily_institution_date "
        "ON campaign_metrics_daily (institution_id, metric_date)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_metrics_daily_workflow_date "
        "ON campaign_metrics_daily (workflow_id, metric_date)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_metrics_daily_location_date "
        "ON campaign_metrics_daily (location_id, metric_date)",
    ):
        op.execute(stmt)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_outcome_definitions (
            id             serial PRIMARY KEY,
            category       varchar(80) NOT NULL,
            outcome_key    varchar(80) NOT NULL,
            label          varchar(120) NOT NULL,
            "group"        varchar(24) NOT NULL,
            description    varchar(240),
            sort_order     integer NOT NULL DEFAULT 0,
            created_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_outcome_definitions_category_key
                UNIQUE (category, outcome_key)
        )
        """
    )
    op.execute(
        """
        INSERT INTO campaign_outcome_definitions
            (category, outcome_key, label, "group", description, sort_order)
        VALUES
            ('appointment_confirmation', 'confirmed', 'Confirmed', 'success', 'Patient confirmed the appointment.', 10),
            ('appointment_confirmation', 'reschedule_requested', 'Reschedule Requested', 'neutral', 'Patient asked staff to move the appointment.', 20),
            ('appointment_confirmation', 'staff_handoff', 'Staff Handoff', 'neutral', 'Automation routed the run to a human.', 30),
            ('appointment_confirmation', 'opt_out', 'Opt-Out', 'failure', 'Patient opted out of campaign communication.', 40),
            ('recall', 'booked', 'Recall Booked', 'success', 'Patient booked from recall outreach.', 10),
            ('recall', 'callback_requested', 'Callback Requested', 'neutral', 'Patient asked for staff follow-up.', 20),
            ('recall', 'staff_handoff', 'Staff Handoff', 'neutral', 'Automation routed the run to a human.', 30),
            ('callback', 'callback_requested', 'Callback Requests', 'neutral', 'Callbacks entering the campaign.', 10),
            ('callback', 'booked', 'Booked By Callback', 'success', 'Callback automation or staff produced a booking.', 20),
            ('callback', 'staff_handoff', 'Staff Handoff', 'neutral', 'Automation routed the run to a human.', 30),
            ('treatment', 'booked', 'Treatment Visit Booked', 'success', 'Patient scheduled the next treatment-related visit.', 10),
            ('treatment', 'callback_requested', 'Callback Requested', 'neutral', 'Patient asked for staff follow-up.', 20),
            ('treatment', 'staff_handoff', 'Staff Handoff', 'neutral', 'Automation routed the run to a human.', 30),
            ('reactivation', 'booked', 'Reactivation Booked', 'success', 'Patient scheduled a visit after reactivation outreach.', 10),
            ('default', 'confirmed', 'Confirmed', 'success', 'Patient confirmed.', 10),
            ('default', 'booked', 'Booked', 'success', 'Patient booked.', 20),
            ('default', 'callback_requested', 'Callback Requested', 'neutral', 'Patient asked for a callback.', 30),
            ('default', 'staff_handoff', 'Staff Handoff', 'neutral', 'Automation routed the run to a human.', 40),
            ('default', 'opt_out', 'Opt-Out', 'failure', 'Patient opted out.', 50)
        ON CONFLICT (category, outcome_key) DO NOTHING
        """
    )

    expr = _metrics_rls_expr(METRICS_TABLE)
    op.execute(f"ALTER TABLE {METRICS_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {METRICS_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {METRICS_TABLE}_rls ON {METRICS_TABLE}")
    op.execute(
        f"CREATE POLICY {METRICS_TABLE}_rls ON {METRICS_TABLE} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    _grant(METRICS_TABLE)
    _grant(DEFINITIONS_TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {METRICS_TABLE}_rls ON {METRICS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {METRICS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {DEFINITIONS_TABLE}")
