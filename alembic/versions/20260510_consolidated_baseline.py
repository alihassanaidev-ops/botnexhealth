"""Consolidated baseline — entire schema + RLS + audit hardening in one migration.

Revision ID: 20260510_baseline
Revises: None
Create Date: 2026-05-10

Squashes 45 prior migrations (0001 through 20260509_narrow_auth_users)
into a single self-contained baseline. Anyone running
``alembic upgrade head`` against a fresh database lands here with:

  - Full schema from ``src.app.models`` (28 tables + indexes + FKs)
  - Audit-log immutability triggers (HIPAA §164.312(b))
  - Enum / role check constraints
  - RLS helper functions (sql + plpgsql + SECURITY DEFINER)
  - ``app_rls_definer`` role with BYPASSRLS
  - ENABLE + FORCE ROW LEVEL SECURITY on 24 tenant-scoped tables
  - 28 row-level security policies including narrow-by-helper for
    retell_lookup / twilio_lookup / auth_email / auth_reset_token /
    auth_invite_token contexts (defense-in-depth verified live)

After this migration, alembic is the single source of truth for
schema. ``Base.metadata.create_all`` is no longer used by bootstrap
or any production path — running ``alembic upgrade head`` against
an empty DB lands a fully-correct schema.

Requires the migration runner (i.e. ``DATABASE_ADMIN_URL``) to:
  - Have ``CREATEROLE`` and the ability to grant ``BYPASSRLS``
    (i.e. SUPERUSER or rds_superuser).
  - Have ``CREATE`` on the ``public`` schema.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from src.app.database import Base
from src.app.models import *  # noqa: F401,F403 - populate metadata
from src.app.models.user import User  # noqa: F401 - not in models.__all__


revision: str = "20260510_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Tables protected by FORCE ROW LEVEL SECURITY
# ---------------------------------------------------------------------------

PROTECTED_TABLES: tuple[str, ...] = (
    "institutions",
    "institution_locations",
    "institution_providers",
    "institution_appointment_types",
    "institution_descriptors",
    "institution_operatories",
    "institution_location_transfer_numbers",
    "insurance_plans",
    "location_operating_hours",
    "location_breaks",
    "users",
    "contacts",
    "contact_location_accesses",
    "calls",
    "custom_field_definitions",
    "custom_field_values",
    "notifications",
    "user_email_notification_preferences",
    "email_templates",
    "external_notification_recipients",
    "sms_history_logs",
    "consent_records",
    "sms_suppressions",
    "do_not_contact",
    "audit_logs",
    "dead_letter_events",
    "retell_webhook_events",
    "retell_function_invocations",
    "workflow_statuses",
    # call_metrics_daily is added in migration 20260513_metrics; it is
    # institution-scoped and RLS-enabled at table-creation time. Listing
    # it here satisfies the
    # tests/unit/test_rls_protected_tables_coverage.py invariant.
    "call_metrics_daily",
    # Outbound automation workflow engine tables are added by
    # 20260702_auto_workflow_core for existing databases. They are
    # listed here too because the baseline creates all current SQLAlchemy
    # models on fresh local/test databases.
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_runs",
    "automation_workflow_step_executions",
    "automation_workflow_timers",
    "automation_workflow_events",
    # Plan 12 compliance gate halt table — added by 20260703_outbound_halt
    # for existing databases; listed here so fresh databases get RLS too.
    "outbound_emergency_halts",
)


# ---------------------------------------------------------------------------
# Audit-log immutability (HIPAA §164.312(b))
# ---------------------------------------------------------------------------

_AUDIT_TRIGGERS_SQL: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs table is append-only. UPDATE and DELETE are prohibited (HIPAA §164.312(b)).';
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;",
    """
    CREATE TRIGGER audit_logs_no_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation();
    """,
    "DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;",
    """
    CREATE TRIGGER audit_logs_no_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation();
    """,
    "ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_actor_check;",
    """
    ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_actor_check
        CHECK (actor IN ('RETELL_AGENT', 'ADMIN', 'SYSTEM', 'API_CLIENT'));
    """,
)


# ---------------------------------------------------------------------------
# Enum check constraints (one per enum-shaped column not handled by
# __table_args__ on the model)
# ---------------------------------------------------------------------------

_ENUM_CHECKS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "ck_users_role",
        "users",
        "role",
        ("SUPER_ADMIN", "INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"),
    ),
    (
        "ck_calls_call_status",
        "calls",
        "call_status",
        (
            "appointment_booked",
            "appointment_rescheduled",
            "appointment_cancelled",
            "emergency",
            "complaint",
            "needs_callback",
            "faq_handled",
            "financial_inquiry",
            "transferred",
            "insurance_verified",
            "insurance_unverified",
            "no_action_needed",
        ),
    ),
    ("ck_calls_call_direction", "calls", "call_direction", ("inbound", "outbound")),
    (
        "ck_custom_field_definitions_entity_type",
        "custom_field_definitions",
        "entity_type",
        ("contact", "call"),
    ),
    (
        "ck_custom_field_definitions_field_type",
        "custom_field_definitions",
        "field_type",
        ("text", "number", "boolean", "date", "dropdown"),
    ),
    (
        "ck_notifications_type",
        "notifications",
        "type",
        (
            "new_call",
            "callback_item",
            "callback_resolved",
            "appointment_booked",
            "urgent",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Partial unique indexes (postgresql_where) not directly model-derivable
# ---------------------------------------------------------------------------

_PARTIAL_UNIQUE_INDEXES_SQL: tuple[str, ...] = (
    # Email is unique among ACTIVE users only — soft-deleted preserved
    # indefinitely for audit FK integrity.
    "DROP INDEX IF EXISTS ix_users_email_active;",
    """
    CREATE UNIQUE INDEX ix_users_email_active ON users (email)
    WHERE deleted_at IS NULL;
    """,
)


# ---------------------------------------------------------------------------
# RLS plain helpers (LANGUAGE sql STABLE — safe to inline; just read GUCs)
# ---------------------------------------------------------------------------

_RLS_PLAIN_HELPERS_SQL: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION app_rls_setting(setting_name text)
    RETURNS text LANGUAGE sql STABLE AS $$
        SELECT NULLIF(current_setting(setting_name, true), '')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_uuid(setting_name text)
    RETURNS uuid LANGUAGE plpgsql STABLE AS $$
    BEGIN
        RETURN NULLIF(current_setting(setting_name, true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN NULL;
    END;
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_context_type()
    RETURNS text LANGUAGE sql STABLE AS $$
        SELECT app_rls_setting('app.context_type')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_role()
    RETURNS text LANGUAGE sql STABLE AS $$
        SELECT app_rls_setting('app.role')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_user_id()
    RETURNS uuid LANGUAGE sql STABLE AS $$
        SELECT app_rls_uuid('app.user_id')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_institution_id()
    RETURNS uuid LANGUAGE sql STABLE AS $$
        SELECT app_rls_uuid('app.institution_id')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_location_id()
    RETURNS uuid LANGUAGE sql STABLE AS $$
        SELECT app_rls_uuid('app.location_id')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_external_id()
    RETURNS text LANGUAGE sql STABLE AS $$
        SELECT app_rls_setting('app.external_id')
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_is_super_admin()
    RETURNS boolean LANGUAGE sql STABLE AS $$
        SELECT app_rls_context_type() = 'user' AND app_rls_role() = 'SUPER_ADMIN'
    $$;
    """,
)


# ---------------------------------------------------------------------------
# BYPASSRLS role for SECURITY DEFINER helper ownership
# ---------------------------------------------------------------------------

_DEFINER_ROLE_DDL: str = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rls_definer') THEN
        CREATE ROLE app_rls_definer WITH NOLOGIN BYPASSRLS;
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'app_rls_definer' AND rolbypassrls
    ) THEN
        ALTER ROLE app_rls_definer WITH BYPASSRLS;
    END IF;
END $$;
"""


# ---------------------------------------------------------------------------
# SECURITY DEFINER helpers (LANGUAGE plpgsql to defeat planner inlining —
# SQL+STABLE+SECURITY DEFINER targeting the same table its policy is on
# gets inlined and produces a recursive plan that asyncpg's prepared-
# statement path blows up on with "stack depth limit exceeded")
# ---------------------------------------------------------------------------

_RLS_SECURITY_DEFINER_HELPERS_SQL: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION app_rls_inst_for_retell_agent(agent text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF agent IS NULL OR agent = '' THEN
            RETURN NULL;
        END IF;
        SELECT institution_id INTO result FROM institution_locations
        WHERE retell_agent_id = agent LIMIT 1;
        RETURN result;
    END $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_inst_for_twilio_number(num text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF num IS NULL OR num = '' THEN
            RETURN NULL;
        END IF;
        SELECT institution_id INTO result FROM institution_locations
        WHERE twilio_from_number = num LIMIT 1;
        RETURN result;
    END $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_user_for_email(addr text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF addr IS NULL OR addr = '' THEN
            RETURN NULL;
        END IF;
        SELECT id INTO result FROM users
        WHERE email = addr AND deleted_at IS NULL LIMIT 1;
        RETURN result;
    END $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_user_for_reset_token(h text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF h IS NULL OR h = '' THEN
            RETURN NULL;
        END IF;
        SELECT id INTO result FROM users
        WHERE password_reset_token_hash = h AND deleted_at IS NULL LIMIT 1;
        RETURN result;
    END $$;
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_user_for_invite_token(h text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF h IS NULL OR h = '' THEN
            RETURN NULL;
        END IF;
        SELECT id INTO result FROM users
        WHERE invite_token_hash = h AND deleted_at IS NULL LIMIT 1;
        RETURN result;
    END $$;
    """,
)


# Reassign ownership to BYPASSRLS role + grant SELECT so SECURITY DEFINER
# bodies can actually read. Without this the helper's inner SELECT fails
# permission-denied OR (if owner has no BYPASSRLS) recurses through RLS.
_DEFINER_OWNERSHIP_AND_GRANTS_SQL: tuple[str, ...] = (
    "ALTER FUNCTION app_rls_inst_for_retell_agent(text) OWNER TO app_rls_definer;",
    "ALTER FUNCTION app_rls_inst_for_twilio_number(text) OWNER TO app_rls_definer;",
    "ALTER FUNCTION app_rls_user_for_email(text) OWNER TO app_rls_definer;",
    "ALTER FUNCTION app_rls_user_for_reset_token(text) OWNER TO app_rls_definer;",
    "ALTER FUNCTION app_rls_user_for_invite_token(text) OWNER TO app_rls_definer;",
    "GRANT SELECT ON institution_locations TO app_rls_definer;",
    "GRANT SELECT ON users TO app_rls_definer;",
)


# ---------------------------------------------------------------------------
# RLS policy expressions, keyed by table
# ---------------------------------------------------------------------------


def _scoped_system_contexts() -> str:
    return (
        "'auth', 'audit', 'retell', 'retell_lookup', 'retell_function', "
        "'celery', 'twilio', 'twilio_lookup', 'twilio_status', "
        "'dead_letter', 'middleware_lookup'"
    )


def _institution_owned_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def _location_scoped_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR app_rls_context_type() IN ({_scoped_system_contexts()})
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


def _automation_workflow_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
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


def _location_only_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR app_rls_context_type() IN ({_scoped_system_contexts()})
        OR (
            app_rls_context_type() = 'user'
            AND EXISTS (
                SELECT 1 FROM institution_locations il
                WHERE il.id = {table}.location_id
                  AND il.institution_id = app_rls_institution_id()
                  AND (
                    app_rls_role() = 'INSTITUTION_ADMIN'
                    OR il.id = app_rls_location_id()
                  )
            )
        )
    """


def _outbound_halt_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND outbound_emergency_halts.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND outbound_emergency_halts.institution_id = app_rls_institution_id()
            AND app_rls_role() = 'INSTITUTION_ADMIN'
        )
    """


def _institutions_expr() -> str:
    return """
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
        OR (
            app_rls_context_type() = 'user'
            AND institutions.id = app_rls_institution_id()
        )
    """


def _institution_locations_expr() -> str:
    return """
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
        OR (
            app_rls_context_type() = 'user'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR institution_locations.id = app_rls_location_id()
            )
        )
    """


def _users_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'auth'
            AND users.id = app_rls_user_id()
        )
        OR (
            app_rls_context_type() = 'auth_email'
            AND users.id = app_rls_user_for_email(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'auth_reset_token'
            AND users.id = app_rls_user_for_reset_token(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'auth_invite_token'
            AND users.id = app_rls_user_for_invite_token(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'user'
            AND (
                users.id = app_rls_user_id()
                OR (
                    users.institution_id = app_rls_institution_id()
                    AND app_rls_role() = 'INSTITUTION_ADMIN'
                )
                OR (
                    users.institution_id = app_rls_institution_id()
                    AND users.location_id = app_rls_location_id()
                    AND app_rls_role() = 'LOCATION_ADMIN'
                )
            )
        )
        OR (
            app_rls_context_type() IN ('celery', 'twilio', 'retell', 'dead_letter')
            AND users.institution_id = app_rls_institution_id()
        )
    """


def _contacts_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND contacts.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND contacts.institution_id = app_rls_institution_id()
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


def _contact_access_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND contact_location_accesses.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND contact_location_accesses.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR contact_location_accesses.location_id = app_rls_location_id()
            )
        )
    """


def _calls_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'dead_letter')
            AND (
                calls.institution_id = app_rls_institution_id()
                OR calls.retell_call_id = app_rls_external_id()
                OR calls.id::text = app_rls_external_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND calls.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR calls.location_id = app_rls_location_id()
            )
        )
    """


def _custom_values_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND custom_field_values.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND custom_field_values.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR (
                    custom_field_values.entity_type = 'call'
                    AND EXISTS (
                        SELECT 1 FROM calls c
                        WHERE c.id = custom_field_values.entity_id
                          AND c.location_id = app_rls_location_id()
                    )
                )
                OR (
                    custom_field_values.entity_type = 'contact'
                    AND EXISTS (
                        SELECT 1 FROM contact_location_accesses cla
                        WHERE cla.contact_id = custom_field_values.entity_id
                          AND cla.location_id = app_rls_location_id()
                    )
                )
            )
        )
    """


def _notifications_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND notifications.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND notifications.institution_id = app_rls_institution_id()
            AND notifications.user_id = app_rls_user_id()
        )
    """


def _user_prefs_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR app_rls_context_type() = 'celery'
        OR (
            app_rls_context_type() = 'user'
            AND user_email_notification_preferences.user_id = app_rls_user_id()
        )
    """


def _sms_history_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'twilio_status'
            AND sms_history_logs.message_sid = app_rls_external_id()
        )
        OR (
            app_rls_context_type() IN ('celery', 'twilio', 'dead_letter')
            AND (
                sms_history_logs.institution_id = app_rls_institution_id()
                OR sms_history_logs.location_id = app_rls_location_id()
                OR sms_history_logs.institution_location_id::text = app_rls_external_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND sms_history_logs.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR sms_history_logs.location_id = app_rls_location_id()
            )
        )
    """


def _audit_logs_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR app_rls_context_type() = 'audit'
        OR (
            app_rls_context_type() = 'user'
            AND audit_logs.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR audit_logs.location_id = app_rls_location_id()
                OR audit_logs.user_id = app_rls_user_id()
            )
        )
    """


def _dead_letter_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR app_rls_context_type() = 'dead_letter'
        OR (
            app_rls_context_type() = 'user'
            AND dead_letter_events.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR dead_letter_events.location_id = app_rls_location_id()
            )
        )
    """


def _retell_events_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'retell_function')
            AND (
                {table}.call_id = app_rls_external_id()
                OR {table}.institution_id = app_rls_institution_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND app_rls_role() = 'INSTITUTION_ADMIN'
        )
    """


def _policy_stmts(table: str, expr: str) -> tuple[str, str]:
    """Return DROP IF EXISTS + CREATE statements separately.

    asyncpg's prepared-statement protocol forbids multiple commands in
    one ``op.execute(...)`` call, so each statement must be its own
    string.
    """
    return (
        f"DROP POLICY IF EXISTS {table}_rls ON {table};",
        f"CREATE POLICY {table}_rls ON {table} FOR ALL "
        f"USING ({expr}) WITH CHECK ({expr});",
    )


def _all_policies_sql() -> tuple[str, ...]:
    spec: tuple[tuple[str, str], ...] = (
        ("institutions", _institutions_expr()),
        ("institution_locations", _institution_locations_expr()),
        ("institution_providers", _location_scoped_expr("institution_providers")),
        (
            "institution_appointment_types",
            _location_scoped_expr("institution_appointment_types"),
        ),
        ("institution_descriptors", _location_scoped_expr("institution_descriptors")),
        ("institution_operatories", _location_scoped_expr("institution_operatories")),
        (
            "institution_location_transfer_numbers",
            _location_scoped_expr("institution_location_transfer_numbers"),
        ),
        ("insurance_plans", _location_scoped_expr("insurance_plans")),
        ("location_operating_hours", _location_only_expr("location_operating_hours")),
        ("location_breaks", _location_only_expr("location_breaks")),
        ("users", _users_expr()),
        ("contacts", _contacts_expr()),
        ("contact_location_accesses", _contact_access_expr()),
        ("calls", _calls_expr()),
        (
            "custom_field_definitions",
            _institution_owned_expr("custom_field_definitions"),
        ),
        ("custom_field_values", _custom_values_expr()),
        ("notifications", _notifications_expr()),
        ("user_email_notification_preferences", _user_prefs_expr()),
        ("email_templates", _institution_owned_expr("email_templates")),
        (
            "external_notification_recipients",
            _institution_owned_expr("external_notification_recipients"),
        ),
        ("sms_history_logs", _sms_history_expr()),
        ("consent_records", _location_scoped_expr("consent_records")),
        ("sms_suppressions", _location_scoped_expr("sms_suppressions")),
        ("do_not_contact", _location_scoped_expr("do_not_contact")),
        ("audit_logs", _audit_logs_expr()),
        ("dead_letter_events", _dead_letter_expr()),
        ("retell_webhook_events", _retell_events_expr("retell_webhook_events")),
        (
            "retell_function_invocations",
            _retell_events_expr("retell_function_invocations"),
        ),
        ("workflow_statuses", _institution_owned_expr("workflow_statuses")),
        ("automation_workflows", _automation_workflow_expr("automation_workflows")),
        (
            "automation_workflow_versions",
            _automation_workflow_expr("automation_workflow_versions"),
        ),
        ("automation_workflow_runs", _automation_workflow_expr("automation_workflow_runs")),
        (
            "automation_workflow_step_executions",
            _automation_workflow_expr("automation_workflow_step_executions"),
        ),
        (
            "automation_workflow_timers",
            _automation_workflow_expr("automation_workflow_timers"),
        ),
        (
            "automation_workflow_events",
            _automation_workflow_expr("automation_workflow_events"),
        ),
        ("outbound_emergency_halts", _outbound_halt_expr()),
    )
    out: list[str] = []
    for table, expr in spec:
        for stmt in _policy_stmts(table, expr):
            out.append(stmt)
    return tuple(out)


_POLICIES_SQL = _all_policies_sql()


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Schema — every table, index, FK, and CheckConstraint declared in
    #    the model layer.
    Base.metadata.create_all(bind)

    # 2. Audit-log immutability (HIPAA §164.312(b))
    for stmt in _AUDIT_TRIGGERS_SQL:
        op.execute(stmt)

    # 3. Enum check constraints not declared in models
    for name, table, column, values in _ENUM_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};")
        allowed = ", ".join(f"'{v}'" for v in values)
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} IN ({allowed}));"
        )

    # 4. Partial unique indexes (postgresql_where) not directly model-derivable
    for stmt in _PARTIAL_UNIQUE_INDEXES_SQL:
        op.execute(stmt)

    # 5. RLS plain helpers
    for stmt in _RLS_PLAIN_HELPERS_SQL:
        op.execute(stmt)

    # 6. BYPASSRLS role for SECURITY DEFINER ownership
    op.execute(_DEFINER_ROLE_DDL)

    # 7. SECURITY DEFINER helpers (plpgsql, not inlinable)
    for stmt in _RLS_SECURITY_DEFINER_HELPERS_SQL:
        op.execute(stmt)

    # 8. Definer ownership transfer + grants
    for stmt in _DEFINER_OWNERSHIP_AND_GRANTS_SQL:
        op.execute(stmt)

    # 9. Enable + force RLS on every protected table
    for table in PROTECTED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # 10. Policies
    for stmt in _POLICIES_SQL:
        op.execute(stmt)


def downgrade() -> None:
    """Baseline migration — no downgrade target.

    Refuses rather than dropping the entire schema. If you need to roll
    back, drop the database and start over.
    """
    raise NotImplementedError(
        "Cannot downgrade past baseline. Drop the database to roll back."
    )
