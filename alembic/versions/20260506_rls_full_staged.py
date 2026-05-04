"""Add staged PostgreSQL row-level security policies.

Revision ID: 20260506_rls_full_staged
Revises: 20260505_encrypt_notifications
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260506_rls_full_staged"
down_revision: Union[str, None] = "20260505_encrypt_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROTECTED_TABLES = (
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
)


def upgrade() -> None:
    _add_scope_columns()
    _backfill_scope_columns()
    _create_rls_helpers()
    _enable_rls()
    _create_policies()


def downgrade() -> None:
    for table in PROTECTED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP FUNCTION IF EXISTS app_rls_external_id();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_location_id();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_institution_id();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_user_id();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_role();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_context_type();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_is_super_admin();")
    op.execute("DROP FUNCTION IF EXISTS app_rls_setting(text);")
    op.execute("DROP FUNCTION IF EXISTS app_rls_uuid(text);")

    op.drop_table("contact_location_accesses")
    op.drop_index("ix_sms_history_logs_location_id", table_name="sms_history_logs")
    op.drop_index("ix_sms_history_logs_institution_id", table_name="sms_history_logs")
    op.drop_column("sms_history_logs", "location_id")
    op.drop_column("sms_history_logs", "institution_id")
    op.drop_index("ix_calls_location_id", table_name="calls")
    op.drop_column("calls", "location_id")


def _add_scope_columns() -> None:
    op.add_column(
        "calls",
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_index("ix_calls_location_id", "calls", ["location_id"])
    op.create_foreign_key(
        "calls_location_id_fkey",
        "calls",
        "institution_locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "sms_history_logs",
        sa.Column("institution_id", UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "sms_history_logs",
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_index(
        "ix_sms_history_logs_institution_id",
        "sms_history_logs",
        ["institution_id"],
    )
    op.create_index(
        "ix_sms_history_logs_location_id",
        "sms_history_logs",
        ["location_id"],
    )
    op.create_foreign_key(
        "sms_history_logs_institution_id_fkey",
        "sms_history_logs",
        "institutions",
        ["institution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "sms_history_logs_location_id_fkey",
        "sms_history_logs",
        "institution_locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "contact_location_accesses",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("institution_id", UUID(as_uuid=False), nullable=False),
        sa.Column("contact_id", UUID(as_uuid=False), nullable=False),
        sa.Column("location_id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["institution_locations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "contact_id",
            "location_id",
            name="uq_contact_location_access_contact_location",
        ),
    )
    op.create_index(
        "ix_contact_location_access_institution",
        "contact_location_accesses",
        ["institution_id"],
    )
    op.create_index(
        "ix_contact_location_access_location",
        "contact_location_accesses",
        ["location_id"],
    )
    op.create_index(
        "ix_contact_location_access_contact",
        "contact_location_accesses",
        ["contact_id"],
    )


def _backfill_scope_columns() -> None:
    op.execute(
        """
        UPDATE calls AS c
        SET location_id = il.id
        FROM institution_locations AS il
        WHERE c.location_id IS NULL
          AND c.institution_id = il.institution_id
          AND c.agent_used = il.retell_agent_id
          AND il.retell_agent_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE sms_history_logs AS s
        SET institution_id = il.institution_id,
            location_id = il.id
        FROM institution_locations AS il
        WHERE s.institution_location_id = il.id
        """
    )
    op.execute(
        """
        INSERT INTO contact_location_accesses (id, institution_id, contact_id, location_id)
        SELECT gen_random_uuid(),
               c.institution_id,
               c.contact_id,
               c.location_id
        FROM calls AS c
        WHERE c.contact_id IS NOT NULL
          AND c.location_id IS NOT NULL
        ON CONFLICT (contact_id, location_id) DO NOTHING
        """
    )


def _create_rls_helpers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_setting(setting_name text)
        RETURNS text
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting(setting_name, true), '')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_uuid(setting_name text)
        RETURNS uuid
        LANGUAGE plpgsql
        STABLE
        AS $$
        BEGIN
            RETURN NULLIF(current_setting(setting_name, true), '')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_context_type()
        RETURNS text LANGUAGE sql STABLE AS $$
            SELECT app_rls_setting('app.context_type')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_role()
        RETURNS text LANGUAGE sql STABLE AS $$
            SELECT app_rls_setting('app.role')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_user_id()
        RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT app_rls_uuid('app.user_id')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_institution_id()
        RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT app_rls_uuid('app.institution_id')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_location_id()
        RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT app_rls_uuid('app.location_id')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_external_id()
        RETURNS text LANGUAGE sql STABLE AS $$
            SELECT app_rls_setting('app.external_id')
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_is_super_admin()
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT app_rls_context_type() = 'user' AND app_rls_role() = 'SUPER_ADMIN'
        $$;
        """
    )


def _enable_rls() -> None:
    for table in PROTECTED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")


def _create_policies() -> None:
    _policy("institutions", _institutions_expr())
    _policy("institution_locations", _institution_locations_expr())
    for table in (
        "institution_providers",
        "institution_appointment_types",
        "institution_descriptors",
        "institution_operatories",
        "institution_location_transfer_numbers",
        "insurance_plans",
    ):
        _policy(table, _location_scoped_expr(table))
    _policy("location_operating_hours", _location_only_expr("location_operating_hours"))
    _policy("location_breaks", _location_only_expr("location_breaks"))
    _policy("users", _users_expr())
    _policy("contacts", _contacts_expr())
    _policy("contact_location_accesses", _contact_access_expr())
    _policy("calls", _calls_expr())
    _policy("custom_field_definitions", _institution_owned_expr("custom_field_definitions"))
    _policy("custom_field_values", _custom_values_expr())
    _policy("notifications", _notifications_expr())
    _policy("user_email_notification_preferences", _user_prefs_expr())
    _policy("email_templates", _institution_owned_expr("email_templates"))
    _policy(
        "external_notification_recipients",
        _institution_owned_expr("external_notification_recipients"),
    )
    _policy("sms_history_logs", _sms_history_expr())
    for table in ("consent_records", "sms_suppressions", "do_not_contact"):
        _policy(table, _location_scoped_expr(table))
    _policy("audit_logs", _audit_logs_expr())
    _policy("dead_letter_events", _dead_letter_expr())
    _policy("retell_webhook_events", _retell_events_expr("retell_webhook_events"))
    _policy(
        "retell_function_invocations",
        _retell_events_expr("retell_function_invocations"),
    )


def _policy(table: str, expr: str) -> None:
    op.execute(
        f"""
        CREATE POLICY {table}_rls ON {table}
        FOR ALL
        USING ({expr})
        WITH CHECK ({expr});
        """
    )


def _scoped_system_contexts() -> str:
    return "'retell', 'celery', 'twilio', 'dead_letter'"


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
        OR (
            app_rls_context_type() IN ({_scoped_system_contexts()})
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
        OR (
            app_rls_context_type() IN ({_scoped_system_contexts()})
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
            AND EXISTS (
                SELECT 1 FROM institution_locations il
                WHERE il.id = {table}.location_id
                  AND il.institution_id = app_rls_institution_id()
            )
        )
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


def _institutions_expr() -> str:
    # retell_lookup / twilio_lookup are used by webhooks BEFORE tenant
    # resolution. NOT a blanket allow on institutions — narrow via the
    # SECURITY DEFINER helpers app_rls_inst_for_retell_agent /
    # app_rls_inst_for_twilio_number (see migration
    # 20260508_narrow_institutions_lookup_rls). The naive EXISTS-against-
    # institution_locations pattern deadlocks because that table's own
    # policy already EXISTS against institutions for middleware_lookup,
    # so Postgres detects infinite recursion in row-security policies.
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
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
    # The 'auth' context covers login, password reset, refresh, and set-password
    # flows. When user_id is set (e.g., the deps.py JWT-validated path after a
    # token has been verified) the policy narrows to that single user. When
    # user_id is unset (the email/token-lookup path during login or reset, where
    # the caller doesn't yet know which user row matches) we let the
    # application-layer email/token filter through; otherwise authentication
    # would always return zero rows and break login entirely.
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'auth'
            AND (app_rls_user_id() IS NULL OR users.id = app_rls_user_id())
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
        OR (
            app_rls_context_type() = 'celery'
            AND EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = user_email_notification_preferences.user_id
                  AND u.institution_id = app_rls_institution_id()
            )
        )
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
        OR (
            app_rls_context_type() = 'audit'
            AND (
                audit_logs.institution_id = app_rls_institution_id()
                OR (
                    app_rls_institution_id() IS NULL
                    AND audit_logs.institution_id IS NULL
                )
            )
        )
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
        OR (
            app_rls_context_type() = 'dead_letter'
            AND (
                dead_letter_events.institution_id = app_rls_institution_id()
                OR (
                    app_rls_institution_id() IS NULL
                    AND dead_letter_events.institution_id IS NULL
                )
            )
            AND (
                app_rls_location_id() IS NULL
                OR dead_letter_events.location_id = app_rls_location_id()
            )
        )
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
