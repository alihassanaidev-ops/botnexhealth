"""Workflow Split (A/B) node: run assignments and the per-variant rollup.

Two tables:

* ``automation_workflow_split_assignments`` — which arm of which split node a
  run took. The arm is derivable from the run id without storage, so this exists
  for the rollup's SQL join and for the audit trail: it pins what a contact was
  actually sent, so editing a split's weights later cannot rewrite the results
  of contacts who already went through the old ones.
* ``campaign_split_metrics_daily`` — the campaign rollup cut by split node and
  arm. Same columns as ``campaign_metrics_daily``, two extra key columns, and it
  is written by the same rendered-twice union in the same transaction, so an
  arm's numbers always reconcile against the campaign total.

Both statements are idempotent. ``20260510_consolidated_baseline`` builds the
schema with ``Base.metadata.create_all``, so on a database created after these
models exist the tables are already there and this migration is a no-op; on an
existing database it is what creates them.

RLS mirrors the tables each one shadows: the assignment table follows the
automation run policies (celery writes it, clinic staff read their own), and the
metrics table follows ``campaign_metrics_daily``, including the usage_metering
context that recomputes the rollup.

Revision ID: 20260902_workflow_split_ab
Revises: 20260902_merge_qual_forms
"""

from __future__ import annotations

from alembic import op

revision = "20260902_workflow_split_ab"
down_revision = "20260902_merge_qual_forms"
branch_labels = None
depends_on = None

ASSIGNMENTS_TABLE = "automation_workflow_split_assignments"
METRICS_TABLE = "campaign_split_metrics_daily"

def _run_scoped_rls_expr(table: str) -> str:
    """Same shape as the other automation run-scoped tables."""
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


def _metrics_rls_expr(table: str) -> str:
    """Same shape as campaign_metrics_daily, including the metering context.

    ``location_id`` is the all-zero sentinel rather than NULL here, matching the
    table it shadows, so the location comparison is a plain equality.
    """
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
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


def _apply_rls(table: str, expr: str) -> None:
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


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ASSIGNMENTS_TABLE} (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            workflow_id          uuid NOT NULL REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id  uuid NOT NULL
                REFERENCES automation_workflow_versions(id) ON DELETE CASCADE,
            workflow_run_id      uuid NOT NULL
                REFERENCES automation_workflow_runs(id) ON DELETE CASCADE,
            node_id              varchar(120) NOT NULL,
            branch_label         varchar(60) NOT NULL,
            bucket               integer NOT NULL,
            assigned_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_automation_split_assignment_run_node
                UNIQUE (workflow_run_id, node_id),
            CONSTRAINT ck_automation_split_assignments_bucket
                CHECK (bucket >= 0 AND bucket < 100)
        )
        """
    )
    for stmt in (
        f"CREATE INDEX IF NOT EXISTS ix_automation_split_assignments_institution "
        f"ON {ASSIGNMENTS_TABLE} (institution_id)",
        f"CREATE INDEX IF NOT EXISTS ix_automation_split_assignments_version_node "
        f"ON {ASSIGNMENTS_TABLE} (workflow_version_id, node_id, branch_label)",
        # The rollup joins from runs to arms; without this it seq-scans the
        # assignment table once per metric branch.
        f"CREATE INDEX IF NOT EXISTS ix_automation_split_assignments_run "
        f"ON {ASSIGNMENTS_TABLE} (workflow_run_id)",
    ):
        op.execute(stmt)

    # Every metric column of campaign_metrics_daily, plus the two keys that make
    # this the per-variant cut. Spelled out rather than cloned with LIKE: LIKE
    # copies neither the primary key nor the foreign keys, and the shape is held
    # in lockstep by the CampaignMetricColumns mixin the two models share.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {METRICS_TABLE} (
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid NOT NULL,
            workflow_id             uuid NOT NULL
                REFERENCES automation_workflows(id) ON DELETE CASCADE,
            workflow_version_id     uuid NOT NULL
                REFERENCES automation_workflow_versions(id) ON DELETE CASCADE,
            metric_date             date NOT NULL,
            split_node_id           varchar(120) NOT NULL,
            branch_label            varchar(60) NOT NULL,
            enrollments                bigint NOT NULL DEFAULT 0,
            active                     bigint NOT NULL DEFAULT 0,
            completed                  bigint NOT NULL DEFAULT 0,
            failed                     bigint NOT NULL DEFAULT 0,
            cancelled                  bigint NOT NULL DEFAULT 0,
            suppressed                 bigint NOT NULL DEFAULT 0,
            sms_sent                   bigint NOT NULL DEFAULT 0,
            sms_delivered              bigint NOT NULL DEFAULT 0,
            sms_failed                 bigint NOT NULL DEFAULT 0,
            sms_replied                bigint NOT NULL DEFAULT 0,
            voice_attempted            bigint NOT NULL DEFAULT 0,
            voice_answered             bigint NOT NULL DEFAULT 0,
            voice_voicemail            bigint NOT NULL DEFAULT 0,
            voice_failed               bigint NOT NULL DEFAULT 0,
            email_sent                 bigint NOT NULL DEFAULT 0,
            email_delivered            bigint NOT NULL DEFAULT 0,
            email_opened               bigint NOT NULL DEFAULT 0,
            email_clicked              bigint NOT NULL DEFAULT 0,
            email_bounced              bigint NOT NULL DEFAULT 0,
            confirmed                  bigint NOT NULL DEFAULT 0,
            booked                     bigint NOT NULL DEFAULT 0,
            reschedule_requested       bigint NOT NULL DEFAULT 0,
            callback_requested         bigint NOT NULL DEFAULT 0,
            staff_handoff              bigint NOT NULL DEFAULT 0,
            opt_out                    bigint NOT NULL DEFAULT 0,
            qualified                  bigint NOT NULL DEFAULT 0,
            not_qualified              bigint NOT NULL DEFAULT 0,
            unreachable                bigint NOT NULL DEFAULT 0,
            transferred                bigint NOT NULL DEFAULT 0,
            total_cost              numeric(16, 5) NOT NULL DEFAULT 0,
            cost_per_booking        numeric(16, 5),
            cost_per_confirmation   numeric(16, 5),
            currency                varchar(3) NOT NULL DEFAULT 'USD',
            updated_at              timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_campaign_split_metrics_daily PRIMARY KEY (
                institution_id, location_id, workflow_id, workflow_version_id,
                metric_date, split_node_id, branch_label
            )
        )
        """
    )
    for stmt in (
        f"CREATE INDEX IF NOT EXISTS ix_campaign_split_metrics_daily_workflow_date "
        f"ON {METRICS_TABLE} (workflow_id, metric_date)",
        f"CREATE INDEX IF NOT EXISTS ix_campaign_split_metrics_daily_institution_date "
        f"ON {METRICS_TABLE} (institution_id, metric_date)",
    ):
        op.execute(stmt)

    _apply_rls(ASSIGNMENTS_TABLE, _run_scoped_rls_expr(ASSIGNMENTS_TABLE))
    _apply_rls(METRICS_TABLE, _metrics_rls_expr(METRICS_TABLE))
    _grant(ASSIGNMENTS_TABLE)
    _grant(METRICS_TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {METRICS_TABLE}_rls ON {METRICS_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS {ASSIGNMENTS_TABLE}_rls ON {ASSIGNMENTS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {METRICS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {ASSIGNMENTS_TABLE}")
