"""Inbound SMS message log (Plan 04 / S-2).

Persists every inbound SMS reply (encrypted body, hashed/masked phones, intent,
best-effort workflow_run_id). RLS: written by the Twilio webhook (context
'twilio') and by Celery, read under the 'user' context.

Revision ID: 20260709_inbound_sms
Revises: 20260708_nh_webhook_subs
"""

from __future__ import annotations

from alembic import op

revision = "20260709_inbound_sms"
down_revision = "20260708_nh_webhook_subs"
branch_labels = None
depends_on = None

TABLE = "inbound_sms_messages"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('twilio', 'celery', 'dead_letter')
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


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_sms_messages (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            contact_id          uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_run_id     uuid,
            message_sid         varchar(64),
            from_phone_hash     varchar(64),
            from_phone_masked   varchar(32),
            to_phone_masked     varchar(32),
            intent              varchar(20) NOT NULL,
            body_encrypted      text,
            created_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_sms_messages_institution_created "
        "ON inbound_sms_messages (institution_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_sms_messages_contact "
        "ON inbound_sms_messages (contact_id)"
    )

    expr = _rls_expr(TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_sms_messages TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute("DROP TABLE IF EXISTS inbound_sms_messages")
