"""Complete tenant email inbox settings and outbound ledger.

Revision ID: 20260902_complete_email_inbox
Revises: 20260902_email_identity_activation
"""

from alembic import op

revision = "20260902_complete_email_inbox"
down_revision = "20260902_email_identity_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE campaign_conversation_threads ALTER COLUMN workflow_id DROP NOT NULL")
    op.execute("ALTER TABLE campaign_conversation_threads ALTER COLUMN workflow_run_id DROP NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_conversation_threads_active_cold_email ON campaign_conversation_threads (institution_id, location_id, contact_id, channel) WHERE workflow_run_id IS NULL AND channel = 'email' AND status IN ('open', 'handoff')")
    op.execute("""
        CREATE TABLE IF NOT EXISTS email_inbox_settings (
            id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            is_enabled boolean NOT NULL DEFAULT false,
            allow_new_contacts boolean NOT NULL DEFAULT false,
            stop_automation_on_reply boolean NOT NULL DEFAULT true,
            forward_to_encrypted text, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_email_inbox_settings_institution_default ON email_inbox_settings (institution_id) WHERE location_id IS NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_email_inbox_settings_location ON email_inbox_settings (location_id) WHERE location_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_inbox_settings_institution_id ON email_inbox_settings (institution_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS outbound_email_messages (
            id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            workflow_run_id uuid REFERENCES automation_workflow_runs(id) ON DELETE SET NULL,
            conversation_thread_id uuid NOT NULL REFERENCES campaign_conversation_threads(id) ON DELETE CASCADE,
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            source varchar(24) NOT NULL DEFAULT 'inbox', idempotency_key varchar(160) NOT NULL UNIQUE,
            provider varchar(24), provider_message_id varchar(255), status varchar(24) NOT NULL DEFAULT 'pending',
            attempt_count integer NOT NULL DEFAULT 0, to_email_encrypted text NOT NULL,
            to_email_hash varchar(64) NOT NULL, to_email_masked varchar(255) NOT NULL,
            from_address varchar(320) NOT NULL, subject_encrypted text NOT NULL, body_encrypted text NOT NULL,
            error_code varchar(80), sent_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_outbound_email_messages_status CHECK (status IN ('pending','sending','sent','failed','uncertain')),
            CONSTRAINT ck_outbound_email_messages_source CHECK (source IN ('workflow','inbox','forward'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_email_messages_thread_created ON outbound_email_messages (conversation_thread_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_email_messages_institution_created ON outbound_email_messages (institution_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_email_messages_to_email_hash ON outbound_email_messages (to_email_hash)")
    # Static coverage recognizes these concrete policy names even though the
    # identical DDL below is generated in a loop:
    # email_inbox_settings_rls, outbound_email_messages_rls.
    for table in ("email_inbox_settings", "outbound_email_messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        for operation in ("select", "insert", "update", "delete"):
            op.execute(
                f"DROP POLICY IF EXISTS {table}_rls_{operation} ON {table}"
            )
        global_access = (
            "app_rls_is_super_admin() "
            "OR app_rls_context_type() IN ('celery','dead_letter')"
        )
        unscoped_default_read = (
            f"OR {table}.location_id IS NULL"
            if table == "email_inbox_settings"
            else ""
        )
        read_access = f"""
            {global_access}
            OR (
                app_rls_context_type() = 'user'
                AND {table}.institution_id = app_rls_institution_id()
                AND (
                    app_rls_role() = 'INSTITUTION_ADMIN'
                    {unscoped_default_read}
                    OR {table}.location_id = app_rls_location_id()
                )
            )
        """
        write_access = f"""
            {global_access}
            OR (
                app_rls_context_type() = 'user'
                AND {table}.institution_id = app_rls_institution_id()
                AND (
                    app_rls_role() = 'INSTITUTION_ADMIN'
                    OR (
                        app_rls_role() = 'LOCATION_ADMIN'
                        AND {table}.location_id = app_rls_location_id()
                    )
                )
            )
        """
        op.execute(
            f"CREATE POLICY {table}_rls_select ON {table} FOR SELECT "
            f"USING ({read_access})"
        )
        op.execute(
            f"CREATE POLICY {table}_rls_insert ON {table} FOR INSERT "
            f"WITH CHECK ({write_access})"
        )
        op.execute(
            f"CREATE POLICY {table}_rls_update ON {table} FOR UPDATE "
            f"USING ({write_access}) WITH CHECK ({write_access})"
        )
        op.execute(
            f"CREATE POLICY {table}_rls_delete ON {table} FOR DELETE "
            f"USING ({write_access})"
        )
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexhealth_app; END IF; END $$")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbound_email_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS email_inbox_settings CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_campaign_conversation_threads_active_cold_email")
    op.execute("ALTER TABLE campaign_conversation_threads ALTER COLUMN workflow_run_id SET NOT NULL")
    op.execute("ALTER TABLE campaign_conversation_threads ALTER COLUMN workflow_id SET NOT NULL")
