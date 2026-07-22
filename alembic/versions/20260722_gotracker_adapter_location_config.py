"""Add GoTracker synchronizer integration support.

GoTracker is integrated as a PMS adapter that talks to the external
ScaleNexus Synchronizer API. Product keys are scoped per location and encrypted
at rest; the base URL is not secret.
"""

from alembic import op

revision = "20260722_gotracker_adapter_location_config"
down_revision = "20260721_merge_call_scrubbed_nexhealth_durability"
branch_labels = None
depends_on = None


def _gotracker_owned_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'gotracker_webhooks')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def _projection_expr(table: str, *, include_gotracker: bool = True) -> str:
    contexts = [
        "'celery'",
        "'dead_letter'",
        "'nexhealth_webhooks'",
        "'nexhealth_lookup'",
    ]
    if include_gotracker:
        contexts.extend(["'gotracker_webhooks'", "'gotracker_lookup'"])
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ({", ".join(contexts)})
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def _contacts_expr(table: str, *, include_gotracker: bool = True) -> str:
    contexts = ["'retell'", "'celery'", "'twilio'", "'dead_letter'"]
    if include_gotracker:
        contexts.extend(["'gotracker_webhooks'", "'gotracker_lookup'"])
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ({", ".join(contexts)})
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR EXISTS (
                    SELECT 1 FROM contact_location_accesses cla
                    WHERE cla.contact_id = contacts.id
                      AND cla.location_id = app_rls_location_id()
                )
            )
        )
    """


def _contact_access_expr(table: str, *, include_gotracker: bool = True) -> str:
    contexts = ["'retell'", "'celery'", "'twilio'", "'dead_letter'"]
    if include_gotracker:
        contexts.extend(["'gotracker_webhooks'", "'gotracker_lookup'"])
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ({", ".join(contexts)})
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


def _institutions_expr(*, include_gotracker: bool = True) -> str:
    gotracker = """
        OR (
            app_rls_context_type() = 'gotracker_lookup'
            AND EXISTS (
                SELECT 1 FROM institution_locations il
                WHERE il.institution_id = institutions.id
                  AND il.id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institutions.id = app_rls_institution_id()
        )
    """ if include_gotracker else ""
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND institutions.id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'audit'
            AND institutions.id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'middleware_lookup'
            AND institutions.slug = app_rls_external_id()
        )
        OR (
            app_rls_context_type() = 'retell_lookup'
            AND institutions.id = app_rls_inst_for_retell_agent(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'twilio_lookup'
            AND institutions.id = app_rls_inst_for_twilio_number(app_rls_external_id())
        )
        {gotracker}
        OR (
            app_rls_context_type() = 'user'
            AND institutions.id = app_rls_institution_id()
        )
    """


def _institution_locations_expr(*, include_gotracker: bool = True) -> str:
    gotracker = """
        OR (
            app_rls_context_type() = 'gotracker_lookup'
            AND institution_locations.id = app_rls_location_id()
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR institution_locations.id = app_rls_location_id()
            )
        )
    """ if include_gotracker else ""
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'middleware_lookup'
            AND EXISTS (
                SELECT 1 FROM institutions i
                WHERE i.id = institution_locations.institution_id
                  AND i.slug = app_rls_external_id()
            )
        )
        OR (
            app_rls_context_type() = 'retell_lookup'
            AND institution_locations.retell_agent_id = app_rls_external_id()
        )
        OR (
            app_rls_context_type() = 'twilio_lookup'
            AND institution_locations.twilio_from_number = app_rls_external_id()
        )
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND (
                institution_locations.institution_id = app_rls_institution_id()
                OR institution_locations.id = app_rls_location_id()
                OR institution_locations.id::text = app_rls_external_id()
            )
        )
        {gotracker}
        OR (
            app_rls_context_type() = 'user'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR institution_locations.id = app_rls_location_id()
            )
        )
    """


def _apply_rls(table: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
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
        "ALTER TABLE institution_locations ADD COLUMN IF NOT EXISTS "
        "gotracker_base_url varchar(500)"
    )
    op.execute(
        "ALTER TABLE institution_locations ADD COLUMN IF NOT EXISTS "
        "gotracker_product_key_encrypted TEXT"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gotracker_webhook_events (
            id uuid PRIMARY KEY,
            institution_id uuid NOT NULL,
            location_id uuid NULL,
            gotracker_appointment_id varchar(160) NULL,
            gotracker_patient_id varchar(160) NULL,
            event_type varchar(64) NOT NULL,
            dedup_key varchar(300) NOT NULL,
            source_event_id varchar(160) NULL,
            payload_hash varchar(128) NULL,
            status varchar(32) NOT NULL DEFAULT 'PROCESSING',
            attempts integer NOT NULL DEFAULT 1,
            last_error text NULL,
            redacted_payload_encrypted text NULL,
            raw_payload_encrypted text NULL,
            raw_payload_retain_until timestamptz NULL,
            raw_payload_purged_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_gotracker_webhook_events_dedup
                UNIQUE (institution_id, dedup_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_institution_id "
        "ON gotracker_webhook_events (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_location_id "
        "ON gotracker_webhook_events (location_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_appointment "
        "ON gotracker_webhook_events (gotracker_appointment_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_patient "
        "ON gotracker_webhook_events (gotracker_patient_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_status "
        "ON gotracker_webhook_events (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_source_event "
        "ON gotracker_webhook_events (source_event_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_payload_hash "
        "ON gotracker_webhook_events (payload_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_raw_retain "
        "ON gotracker_webhook_events (raw_payload_retain_until)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_events_raw_purged "
        "ON gotracker_webhook_events (raw_payload_purged_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gotracker_webhook_subscriptions (
            id uuid PRIMARY KEY,
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            callback_url varchar(500) NULL,
            event_types jsonb NOT NULL DEFAULT '[]'::jsonb,
            provider_subscription_id varchar(160) NULL,
            status varchar(32) NOT NULL DEFAULT 'pending',
            last_health_check_at timestamptz NULL,
            last_event_at timestamptz NULL,
            error_metadata jsonb NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_gotracker_webhook_subscription_location
                UNIQUE (institution_id, location_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_subscriptions_institution_id "
        "ON gotracker_webhook_subscriptions (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_subscriptions_location_id "
        "ON gotracker_webhook_subscriptions (location_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_webhook_subscriptions_status "
        "ON gotracker_webhook_subscriptions (institution_id, status)"
    )
    _apply_rls("gotracker_webhook_events", _gotracker_owned_expr("gotracker_webhook_events"))
    _apply_rls(
        "gotracker_webhook_subscriptions",
        _gotracker_owned_expr("gotracker_webhook_subscriptions"),
    )
    _apply_rls("appointment_working_set", _projection_expr("appointment_working_set"))
    _apply_rls("patient_working_set", _projection_expr("patient_working_set"))
    _apply_rls("contacts", _contacts_expr("contacts"))
    _apply_rls("contact_location_accesses", _contact_access_expr("contact_location_accesses"))
    _apply_rls("institutions", _institutions_expr())
    _apply_rls("institution_locations", _institution_locations_expr())


def downgrade() -> None:
    _apply_rls("institution_locations", _institution_locations_expr(include_gotracker=False))
    _apply_rls("institutions", _institutions_expr(include_gotracker=False))
    _apply_rls(
        "contact_location_accesses",
        _contact_access_expr("contact_location_accesses", include_gotracker=False),
    )
    _apply_rls("contacts", _contacts_expr("contacts", include_gotracker=False))
    _apply_rls("patient_working_set", _projection_expr("patient_working_set", include_gotracker=False))
    _apply_rls(
        "appointment_working_set",
        _projection_expr("appointment_working_set", include_gotracker=False),
    )
    op.execute(
        "DROP POLICY IF EXISTS gotracker_webhook_subscriptions_rls "
        "ON gotracker_webhook_subscriptions"
    )
    op.execute("DROP TABLE IF EXISTS gotracker_webhook_subscriptions")
    op.execute("DROP POLICY IF EXISTS gotracker_webhook_events_rls ON gotracker_webhook_events")
    op.execute("DROP TABLE IF EXISTS gotracker_webhook_events")
    op.execute(
        "ALTER TABLE institution_locations DROP COLUMN IF EXISTS "
        "gotracker_product_key_encrypted"
    )
    op.execute(
        "ALTER TABLE institution_locations DROP COLUMN IF EXISTS gotracker_base_url"
    )
