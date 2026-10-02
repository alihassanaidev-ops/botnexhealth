"""Add campaign response events and staff handoffs.

Revision ID: 20260714_campaign_responses
Revises: 20260713_campaign_overview_indexes
"""

from __future__ import annotations

from alembic import op

revision = "20260714_campaign_responses"
down_revision = "20260713_campaign_overview_indexes"
branch_labels = None
depends_on = None

RESPONSE_TABLE = "campaign_response_events"
HANDOFF_TABLE = "campaign_staff_handoffs"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('twilio', 'retell', 'celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
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


def _enable_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    _grant(table)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_response_events (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id         uuid REFERENCES automation_workflows(id) ON DELETE SET NULL,
            workflow_run_id     uuid REFERENCES automation_workflow_runs(id) ON DELETE SET NULL,
            contact_id          uuid REFERENCES contacts(id) ON DELETE SET NULL,
            channel             varchar(24) NOT NULL,
            normalized_intent   varchar(80) NOT NULL,
            normalized_outcome  varchar(80),
            source              varchar(80) NOT NULL,
            source_event_id     varchar(160),
            source_event_type   varchar(80),
            confidence          varchar(32) NOT NULL DEFAULT 'deterministic',
            summary             varchar(240),
            raw_body_encrypted  text,
            raw_payload_encrypted text,
            occurred_at         timestamptz NOT NULL DEFAULT now(),
            created_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_campaign_response_events_channel
                CHECK (channel IN ('sms', 'voice', 'email', 'booking_link', 'staff'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_staff_handoffs (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id         uuid REFERENCES automation_workflows(id) ON DELETE SET NULL,
            workflow_run_id     uuid REFERENCES automation_workflow_runs(id) ON DELETE SET NULL,
            contact_id          uuid REFERENCES contacts(id) ON DELETE SET NULL,
            response_event_id   uuid REFERENCES campaign_response_events(id) ON DELETE SET NULL,
            assignee_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
            reason              varchar(80) NOT NULL,
            status              varchar(24) NOT NULL DEFAULT 'open',
            summary             varchar(240),
            due_at              timestamptz,
            resolved_at         timestamptz,
            resolution_outcome  varchar(80),
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_campaign_staff_handoffs_reason
                CHECK (reason IN ('free_text', 'reschedule_requested', 'cancel_requested',
                'clinical_question', 'billing_question', 'automation_failed',
                'ambiguous_response', 'ambiguous_voice_outcome', 'patient_asks_for_staff',
                'failed_booking')),
            CONSTRAINT ck_campaign_staff_handoffs_status
                CHECK (status IN ('open', 'assigned', 'resolved', 'dismissed'))
        )
        """
    )
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_campaign_response_events_institution_created "
        "ON campaign_response_events (institution_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_response_events_run_created "
        "ON campaign_response_events (workflow_run_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_response_events_workflow "
        "ON campaign_response_events (workflow_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_response_events_contact "
        "ON campaign_response_events (contact_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_response_events_source "
        "ON campaign_response_events (institution_id, channel, source_event_id) "
        "WHERE source_event_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_campaign_staff_handoffs_institution_status "
        "ON campaign_staff_handoffs (institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_staff_handoffs_run_created "
        "ON campaign_staff_handoffs (workflow_run_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_campaign_staff_handoffs_workflow_status "
        "ON campaign_staff_handoffs (workflow_id, status)",
    ):
        op.execute(stmt)

    _enable_rls(RESPONSE_TABLE)
    _enable_rls(HANDOFF_TABLE)


def downgrade() -> None:
    for table in (HANDOFF_TABLE, RESPONSE_TABLE):
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
