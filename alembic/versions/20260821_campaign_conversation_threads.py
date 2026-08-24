"""Add run-scoped campaign conversation threads.

Revision ID: 20260821_campaign_threads
Revises: 20260821_hide_operatories
"""

from __future__ import annotations

from alembic import op

revision = "20260821_campaign_threads"
down_revision = "20260821_hide_operatories"
branch_labels = None
depends_on = None

THREAD_TABLE = "campaign_conversation_threads"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_rls ON {table}
        USING (
            app_rls_context_type() IN ('celery', 'dead_letter')
            OR app_rls_role() = 'SUPER_ADMIN'
            OR (
                institution_id = app_rls_institution_id()
                AND (
                    app_rls_location_id() IS NULL
                    OR location_id = app_rls_location_id()
                )
            )
        )
        WITH CHECK (
            app_rls_context_type() IN ('celery', 'dead_letter')
            OR app_rls_role() = 'SUPER_ADMIN'
            OR (
                institution_id = app_rls_institution_id()
                AND (
                    app_rls_location_id() IS NULL
                    OR location_id = app_rls_location_id()
                )
            )
        )
        """
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {THREAD_TABLE} (
            id uuid PRIMARY KEY,
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            workflow_id uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_run_id uuid NOT NULL REFERENCES automation_workflow_runs(id) ON DELETE CASCADE,
            channel varchar(24) NOT NULL,
            reply_key varchar(12),
            status varchar(24) NOT NULL DEFAULT 'open',
            opened_at timestamptz NOT NULL DEFAULT now(),
            last_message_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            completion_reason varchar(80),
            CONSTRAINT ck_campaign_conversation_threads_channel
                CHECK (channel IN ('sms')),
            CONSTRAINT ck_campaign_conversation_threads_status
                CHECK (status IN ('open', 'completed', 'handoff'))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_conversation_threads_active_run_channel
        ON campaign_conversation_threads (workflow_run_id, channel)
        WHERE status IN ('open', 'handoff')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_campaign_conversation_threads_lookup
        ON campaign_conversation_threads
        (institution_id, location_id, contact_id, channel, status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_campaign_conversation_threads_reply_key
        ON campaign_conversation_threads
        (institution_id, location_id, channel, reply_key, status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_campaign_conversation_threads_last_message_at
        ON campaign_conversation_threads (last_message_at)
        """
    )

    for table in (
        "sms_history_logs",
        "inbound_sms_messages",
        "campaign_response_events",
        "campaign_staff_handoffs",
    ):
        op.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS conversation_thread_id uuid
            REFERENCES {THREAD_TABLE}(id) ON DELETE SET NULL
            """
        )
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS ix_{table}_conversation_thread_id
            ON {table} (conversation_thread_id)
            """
        )

    _enable_rls(THREAD_TABLE)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {THREAD_TABLE} TO nexhealth_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in (
        "campaign_staff_handoffs",
        "campaign_response_events",
        "inbound_sms_messages",
        "sms_history_logs",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_conversation_thread_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS conversation_thread_id")

    op.execute(f"DROP TABLE IF EXISTS {THREAD_TABLE}")
