"""Automation workflow engine foundation.

Adds durable schema for outbound workflow authoring, immutable published
versions, contact enrollments, step attempts, scheduler timers, and event
history. This migration intentionally does not add delivery logic.

Revision ID: 20260702_auto_workflow_core
Revises: 20260622_nopms_call_status
"""

from __future__ import annotations

from alembic import op


revision = "20260702_auto_workflow_core"
down_revision = "20260622_nopms_call_status"
branch_labels = None
depends_on = None


AUTOMATION_TABLES: tuple[str, ...] = (
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_runs",
    "automation_workflow_step_executions",
    "automation_workflow_timers",
    "automation_workflow_events",
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
        CREATE TABLE IF NOT EXISTS automation_workflows (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            current_version_id  uuid,
            name                varchar(120) NOT NULL,
            description         text,
            category            varchar(50),
            status              varchar(20) NOT NULL DEFAULT 'draft',
            is_template         boolean NOT NULL DEFAULT false,
            created_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
            published_at        timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_automation_workflows_status
                CHECK (status IN ('draft', 'active', 'paused', 'archived'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_versions (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id             uuid NOT NULL REFERENCES automation_workflows(id)
                                    ON DELETE CASCADE,
            version_number          integer NOT NULL,
            definition              jsonb NOT NULL DEFAULT '{}'::jsonb,
            definition_checksum     varchar(64),
            content_classification  varchar(50),
            published_by_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
            published_at            timestamptz NOT NULL DEFAULT now(),
            created_at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_automation_workflow_versions_workflow_number
                UNIQUE (workflow_id, version_number)
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_automation_workflows_current_version'
            ) THEN
                ALTER TABLE automation_workflows
                ADD CONSTRAINT fk_automation_workflows_current_version
                FOREIGN KEY (current_version_id)
                REFERENCES automation_workflow_versions(id)
                ON DELETE SET NULL;
            END IF;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_runs (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id          uuid NOT NULL REFERENCES automation_workflows(id)
                                 ON DELETE CASCADE,
            workflow_version_id  uuid NOT NULL REFERENCES automation_workflow_versions(id)
                                 ON DELETE RESTRICT,
            contact_id           uuid REFERENCES contacts(id) ON DELETE SET NULL,
            idempotency_key      varchar(200),
            trigger_type         varchar(60),
            trigger_ref_type     varchar(60),
            trigger_ref_id       varchar(160),
            trigger_metadata     jsonb,
            status               varchar(20) NOT NULL DEFAULT 'pending',
            current_step_id      varchar(120),
            outcome              varchar(80),
            blocked_reason       varchar(120),
            started_at           timestamptz,
            completed_at         timestamptz,
            cancelled_at         timestamptz,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_automation_workflow_runs_status
                CHECK (status IN (
                    'pending', 'running', 'waiting', 'completed',
                    'cancelled', 'failed', 'blocked'
                ))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_step_executions (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_run_id      uuid NOT NULL REFERENCES automation_workflow_runs(id)
                                 ON DELETE CASCADE,
            workflow_version_id  uuid NOT NULL REFERENCES automation_workflow_versions(id)
                                 ON DELETE RESTRICT,
            step_id              varchar(120) NOT NULL,
            step_type            varchar(60) NOT NULL,
            status               varchar(20) NOT NULL DEFAULT 'pending',
            attempt_number       integer NOT NULL DEFAULT 1,
            max_attempts         integer NOT NULL DEFAULT 1,
            scheduled_at         timestamptz,
            scheduled_local_at   timestamp,
            scheduled_timezone   varchar(64),
            result_code          varchar(80),
            result_metadata      jsonb,
            error_message        text,
            started_at           timestamptz,
            completed_at         timestamptz,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_automation_step_execution_attempt
                UNIQUE (workflow_run_id, step_id, attempt_number),
            CONSTRAINT ck_automation_workflow_step_executions_status
                CHECK (status IN (
                    'pending', 'running', 'waiting', 'completed',
                    'skipped', 'failed', 'blocked'
                ))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_timers (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_run_id     uuid NOT NULL REFERENCES automation_workflow_runs(id)
                                ON DELETE CASCADE,
            step_execution_id   uuid REFERENCES automation_workflow_step_executions(id)
                                ON DELETE CASCADE,
            due_at              timestamptz NOT NULL,
            due_local_at        timestamp,
            timezone            varchar(64),
            status              varchar(20) NOT NULL DEFAULT 'pending',
            claim_token         varchar(120),
            claimed_at          timestamptz,
            claim_expires_at    timestamptz,
            fired_at            timestamptz,
            cancelled_at        timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_automation_workflow_timers_status
                CHECK (status IN ('pending', 'claimed', 'fired', 'cancelled'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_workflow_events (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_run_id      uuid REFERENCES automation_workflow_runs(id) ON DELETE CASCADE,
            workflow_version_id  uuid REFERENCES automation_workflow_versions(id)
                                 ON DELETE SET NULL,
            event_type           varchar(80) NOT NULL,
            step_id              varchar(120),
            event_metadata       jsonb,
            occurred_at          timestamptz NOT NULL DEFAULT now(),
            created_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_automation_workflows_institution_status "
        "ON automation_workflows (institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflows_location_status "
        "ON automation_workflows (location_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_versions_institution "
        "ON automation_workflow_versions (institution_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_versions_workflow "
        "ON automation_workflow_versions (workflow_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_institution_status "
        "ON automation_workflow_runs (institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_location_status "
        "ON automation_workflow_runs (location_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_contact "
        "ON automation_workflow_runs (institution_id, contact_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_workflow "
        "ON automation_workflow_runs (workflow_id, created_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_workflow_run_idempotency "
        "ON automation_workflow_runs (institution_id, workflow_version_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_automation_step_executions_run "
        "ON automation_workflow_step_executions (workflow_run_id, step_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_step_executions_status "
        "ON automation_workflow_step_executions (institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_timers_due "
        "ON automation_workflow_timers (status, due_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_timers_run "
        "ON automation_workflow_timers (workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_timers_institution_status "
        "ON automation_workflow_timers (institution_id, status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_timer_active_step "
        "ON automation_workflow_timers (step_execution_id) "
        "WHERE step_execution_id IS NOT NULL AND status IN ('pending', 'claimed')",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_events_run "
        "ON automation_workflow_events (workflow_run_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_events_institution "
        "ON automation_workflow_events (institution_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_events_type "
        "ON automation_workflow_events (institution_id, event_type)",
    ):
        op.execute(stmt)

    for table in AUTOMATION_TABLES:
        _enable_rls(table)

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    automation_workflows,
                    automation_workflow_versions,
                    automation_workflow_runs,
                    automation_workflow_step_executions,
                    automation_workflow_timers,
                    automation_workflow_events
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in AUTOMATION_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")

    op.execute("DROP TABLE IF EXISTS automation_workflow_events")
    op.execute("DROP TABLE IF EXISTS automation_workflow_timers")
    op.execute("DROP TABLE IF EXISTS automation_workflow_step_executions")
    op.execute("DROP TABLE IF EXISTS automation_workflow_runs")
    op.execute(
        "ALTER TABLE automation_workflows "
        "DROP CONSTRAINT IF EXISTS fk_automation_workflows_current_version"
    )
    op.execute("DROP TABLE IF EXISTS automation_workflow_versions")
    op.execute("DROP TABLE IF EXISTS automation_workflows")
