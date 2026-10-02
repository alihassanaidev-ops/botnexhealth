"""Retell-generated SMS conversation profiles, sessions, and turn ledger.

Revision ID: 20260826_retell_sms
Revises: 20260825_sms_optout_cancel
"""

from __future__ import annotations

from alembic import op


revision = "20260826_retell_sms"
down_revision = "20260825_sms_optout_cancel"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = (
    "retell_sms_chat_profiles",
    "retell_sms_sessions",
    "retell_sms_turns",
)


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'twilio', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
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


def _enable_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL "
        f"USING ({expr}) WITH CHECK ({expr})"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS retell_sms_chat_profiles (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            retell_agent_id varchar(255) NOT NULL,
            agent_version integer,
            display_name varchar(120) NOT NULL,
            purpose varchar(80),
            allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
            is_active boolean NOT NULL DEFAULT true,
            config jsonb,
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS retell_sms_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            workflow_id uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id uuid NOT NULL REFERENCES automation_workflow_versions(id) ON DELETE CASCADE,
            workflow_run_id uuid NOT NULL REFERENCES automation_workflow_runs(id) ON DELETE CASCADE,
            step_execution_id uuid NOT NULL REFERENCES automation_workflow_step_executions(id) ON DELETE CASCADE,
            conversation_thread_id uuid NOT NULL REFERENCES campaign_conversation_threads(id) ON DELETE CASCADE,
            chat_profile_id uuid NOT NULL REFERENCES retell_sms_chat_profiles(id) ON DELETE RESTRICT,
            step_id varchar(120) NOT NULL,
            retell_chat_id varchar(255),
            retell_agent_id varchar(255) NOT NULL,
            agent_version integer,
            status varchar(32) NOT NULL DEFAULT 'awaiting_user',
            turn_count integer NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            max_expires_at timestamptz NOT NULL,
            last_activity_at timestamptz NOT NULL DEFAULT now(),
            terminal_outcome varchar(80),
            failure_code varchar(80),
            ended_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_retell_sms_sessions_status CHECK (status IN (
                'awaiting_user', 'generating', 'completed', 'handoff',
                'timed_out', 'failed', 'opted_out'
            ))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS retell_sms_turns (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            session_id uuid NOT NULL REFERENCES retell_sms_sessions(id) ON DELETE CASCADE,
            inbound_sms_message_id uuid NOT NULL REFERENCES inbound_sms_messages(id) ON DELETE CASCADE,
            message_sid varchar(64),
            status varchar(24) NOT NULL DEFAULT 'claimed',
            retell_message_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            outbound_sms_history_id uuid REFERENCES sms_history_logs(id) ON DELETE SET NULL,
            failure_code varchar(80),
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT ck_retell_sms_turns_status CHECK (
                status IN ('claimed', 'completed', 'failed', 'ambiguous')
            )
        )
        """
    )

    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_profiles_active_location_purpose "
        "ON retell_sms_chat_profiles (location_id, purpose) "
        "WHERE is_active = true AND purpose IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_profiles_active_agent "
        "ON retell_sms_chat_profiles (retell_agent_id) WHERE is_active = true",
        "CREATE INDEX IF NOT EXISTS ix_retell_sms_profiles_institution_active "
        "ON retell_sms_chat_profiles (institution_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_retell_sms_profiles_location_active "
        "ON retell_sms_chat_profiles (location_id, is_active)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_sessions_active_contact_location "
        "ON retell_sms_sessions (institution_id, location_id, contact_id) "
        "WHERE status IN ('awaiting_user', 'generating')",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_sessions_run_step "
        "ON retell_sms_sessions (workflow_run_id, step_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_sessions_chat_id "
        "ON retell_sms_sessions (retell_chat_id) WHERE retell_chat_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_retell_sms_sessions_thread "
        "ON retell_sms_sessions (conversation_thread_id)",
        "CREATE INDEX IF NOT EXISTS ix_retell_sms_sessions_expiry "
        "ON retell_sms_sessions (status, expires_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_turns_message_sid "
        "ON retell_sms_turns (message_sid) WHERE message_sid IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_retell_sms_turns_inbound "
        "ON retell_sms_turns (inbound_sms_message_id)",
        "CREATE INDEX IF NOT EXISTS ix_retell_sms_turns_session_created "
        "ON retell_sms_turns (session_id, created_at)",
    ):
        op.execute(statement)

    for table in TABLES:
        _enable_rls(table)

    # Existing Retell function routes resolve tenancy from agent id. Extend the
    # security-definer lookup seam so chat agents can use the same signed tools.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_location_for_retell_routing_agent(agent text)
        RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE result uuid;
        BEGIN
            IF agent IS NULL OR agent = '' THEN RETURN NULL; END IF;
            SELECT il.id INTO result FROM institution_locations il
            WHERE il.retell_agent_id = agent AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.id INTO result FROM outbound_voice_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.id INTO result FROM retell_sms_chat_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            RETURN result;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_inst_for_retell_routing_agent(agent text)
        RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE result uuid;
        BEGIN
            IF agent IS NULL OR agent = '' THEN RETURN NULL; END IF;
            SELECT il.institution_id INTO result FROM institution_locations il
            WHERE il.retell_agent_id = agent AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.institution_id INTO result FROM outbound_voice_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.institution_id INTO result FROM retell_sms_chat_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            RETURN result;
        END $$
        """
    )
    op.execute(
        "ALTER FUNCTION app_rls_location_for_retell_routing_agent(text) "
        "OWNER TO app_rls_definer"
    )
    op.execute(
        "ALTER FUNCTION app_rls_inst_for_retell_routing_agent(text) "
        "OWNER TO app_rls_definer"
    )
    op.execute("GRANT SELECT ON retell_sms_chat_profiles TO app_rls_definer")
    op.execute(
        """
        CREATE POLICY retell_sms_chat_profiles_retell_lookup
        ON retell_sms_chat_profiles FOR SELECT
        USING (
            app_rls_context_type() = 'retell_lookup'
            AND retell_sms_chat_profiles.retell_agent_id = app_rls_external_id()
            AND retell_sms_chat_profiles.is_active = true
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    retell_sms_chat_profiles,
                    retell_sms_sessions,
                    retell_sms_turns
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # Restore the pre-chat-agent lookup functions before removing their table.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_location_for_retell_routing_agent(agent text)
        RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE result uuid;
        BEGIN
            IF agent IS NULL OR agent = '' THEN RETURN NULL; END IF;
            SELECT il.id INTO result FROM institution_locations il
            WHERE il.retell_agent_id = agent AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.id INTO result FROM outbound_voice_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            RETURN result;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_inst_for_retell_routing_agent(agent text)
        RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE result uuid;
        BEGIN
            IF agent IS NULL OR agent = '' THEN RETURN NULL; END IF;
            SELECT il.institution_id INTO result FROM institution_locations il
            WHERE il.retell_agent_id = agent AND il.is_active = true LIMIT 1;
            IF result IS NOT NULL THEN RETURN result; END IF;
            SELECT il.institution_id INTO result FROM outbound_voice_profiles p
            JOIN institution_locations il ON il.id = p.location_id
              AND il.institution_id = p.institution_id
            WHERE p.retell_agent_id = agent AND p.is_active = true
              AND il.is_active = true LIMIT 1;
            RETURN result;
        END $$
        """
    )
    op.execute("REVOKE SELECT ON retell_sms_chat_profiles FROM app_rls_definer")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
