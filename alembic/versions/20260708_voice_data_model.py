"""Outbound voice data model (Plan 03 / register V-4).

Adds two durable tables that give outbound campaign voice its own system of record:

- ``outbound_voice_profiles`` — per-location outbound config (Retell agent +
  from-number + free-form config). Resolved by the executor as override-with-fallback.
- ``workflow_voice_attempts`` — one row per placed outbound call attempt, keyed on
  ``retell_call_id`` (unique when present), with masked endpoints, a lifecycle
  ``status``, and the normalized ``dial_outcome``. Attempt/outcome history for the UI
  (V-8) and the substrate for the crash-safe committed claim (P9).

Both tables are RLS-scoped exactly like the other automation tables (same
``_rls_expr`` helper as 20260702_auto_workflow_core).

Idempotent (CREATE TABLE/INDEX IF NOT EXISTS, DROP POLICY IF EXISTS) so it is a
no-op on a fresh deploy where the consolidated baseline already created the tables
from live model metadata (``Base.metadata.create_all``), and a real create on an
existing deploy. RLS/grants are NOT in model metadata, so they always (re)apply.

Revision ID: 20260708_voice_data_model
Revises: 20260707_consent_basis
"""

from __future__ import annotations

from alembic import op

revision = "20260708_voice_data_model"
down_revision = "20260707_consent_basis"
branch_labels = None
depends_on = None


VOICE_TABLES: tuple[str, ...] = (
    "outbound_voice_profiles",
    "workflow_voice_attempts",
)


def _rls_expr(table: str) -> str:
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


def _enable_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_rls ON {table} FOR ALL
        USING ({expr})
        WITH CHECK ({expr})
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_voice_profiles (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            retell_agent_id     varchar(255),
            retell_from_number  varchar(32),
            retell_llm_id       varchar(255),
            display_name        varchar(120),
            is_active           boolean NOT NULL DEFAULT true,
            config              jsonb,
            created_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_voice_attempts (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id        uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id           uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_run_id       uuid NOT NULL REFERENCES automation_workflow_runs(id)
                                  ON DELETE CASCADE,
            step_execution_id     uuid REFERENCES automation_workflow_step_executions(id)
                                  ON DELETE SET NULL,
            step_id               varchar(120) NOT NULL,
            attempt_number        integer NOT NULL DEFAULT 1,
            retell_call_id        varchar(255),
            from_number_masked    varchar(32),
            to_number_masked      varchar(32),
            status                varchar(32) NOT NULL,
            dial_outcome          varchar(32),
            disconnection_reason  varchar(80),
            error_message         text,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_workflow_voice_attempts_status
                CHECK (status IN ('initiating', 'placed', 'awaiting_outcome', 'completed', 'failed')),
            CONSTRAINT ck_workflow_voice_attempts_dial_outcome
                CHECK (dial_outcome IS NULL OR dial_outcome IN (
                    'no_answer', 'busy', 'voicemail', 'answered', 'transferred', 'failed', 'unknown'
                ))
        )
        """
    )

    for stmt in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_voice_profiles_active_location "
        "ON outbound_voice_profiles (location_id) WHERE is_active = true",
        "CREATE INDEX IF NOT EXISTS ix_outbound_voice_profiles_institution_active "
        "ON outbound_voice_profiles (institution_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_outbound_voice_profiles_institution "
        "ON outbound_voice_profiles (institution_id)",
        "CREATE INDEX IF NOT EXISTS ix_outbound_voice_profiles_location "
        "ON outbound_voice_profiles (location_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_voice_attempts_retell_call_id "
        "ON workflow_voice_attempts (retell_call_id) WHERE retell_call_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_workflow_voice_attempts_run "
        "ON workflow_voice_attempts (workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_workflow_voice_attempts_institution_status "
        "ON workflow_voice_attempts (institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_workflow_voice_attempts_institution "
        "ON workflow_voice_attempts (institution_id)",
        "CREATE INDEX IF NOT EXISTS ix_workflow_voice_attempts_location "
        "ON workflow_voice_attempts (location_id)",
    ):
        op.execute(stmt)

    for table in VOICE_TABLES:
        _enable_rls(table)

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    outbound_voice_profiles,
                    workflow_voice_attempts
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in VOICE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute("DROP TABLE IF EXISTS workflow_voice_attempts")
    op.execute("DROP TABLE IF EXISTS outbound_voice_profiles")
