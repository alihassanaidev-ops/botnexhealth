"""Convert audit_logs to a monthly range-partitioned table.

The unpartitioned ``audit_logs`` table grows ~1M rows/day at the
documented ~10 audit rows per call × ~100 calls/clinic/day × 1k
clinics. Combined with the immutability trigger firing per-row,
INSERT throughput degrades hard once the table crosses ~50M rows
and any forensic scan walks an ever-growing single index.

Partition by ``timestamp`` (monthly). PostgreSQL >=13 propagates
indexes, RLS, CHECK constraints, and triggers from the parent
partitioned table to all current + future partitions, so the
operational story stays simple: one schema, one policy, one
trigger.

After this migration:

  * ``audit_logs`` is the parent partitioned table — every query in
    the codebase (``WHERE institution_id = $1 ORDER BY timestamp
    DESC``) gets partition pruning automatically.
  * Initial partitions cover previous month + current + 6 months
    forward + a DEFAULT catch-all.
  * Future months are pre-created by
    ``src/app/scripts/ensure_audit_partitions.py`` running daily.
  * The DEFAULT partition catches misconfigured-clock rows so we
    don't lose audit data, but a metric filter on ``audit_logs_default``
    inserts is the alarm signal.

Revision ID: 20260514_partition_audit_logs
Revises: 20260513_metrics
"""

from __future__ import annotations

from datetime import date

from alembic import op


revision = "20260514_audit_part"
down_revision = "20260513_metrics"
branch_labels = None
depends_on = None


# How many months of partitions to pre-create as part of this migration.
# The maintenance script keeps the rolling window alive after deploy.
_INITIAL_FUTURE_MONTHS = 6
# The previous month is included so that any row arriving with a
# timestamp slightly behind ``now`` (e.g., a Retell webhook delivered
# after a clock skew) lands in a real partition instead of DEFAULT.
_INITIAL_PAST_MONTHS = 1


def _partition_name(year: int, month: int) -> str:
    return f"audit_logs_y{year}_m{month:02d}"


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """Return inclusive-start / exclusive-end ISO date strings."""
    start = date(year, month, 1)
    end = date(year + (month // 12), (month % 12) + 1, 1)
    return start.isoformat(), end.isoformat()


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    new_month_index = month - 1 + delta  # zero-indexed for arithmetic
    return year + new_month_index // 12, (new_month_index % 12) + 1


def upgrade() -> None:
    today = date.today()

    # 1. Detach the immutability triggers + RLS policy + CHECK constraint
    #    so we can rename the existing table without the immutability
    #    trigger blocking the DROP at the end.
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs")
    op.execute("DROP POLICY IF EXISTS audit_logs_rls ON audit_logs")
    op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_actor_check")

    # 2. Drop the existing indexes so their names are free for the
    #    parent partitioned table. ALTER TABLE RENAME does NOT rename
    #    the indexes or the PK constraint attached to the table, so
    #    without this DROP the next CREATE INDEX / PK collides on name.
    for index_name in (
        "ix_audit_logs_action",
        "ix_audit_logs_actor",
        "ix_audit_logs_institution_id",
        "ix_audit_logs_location_id",
        "ix_audit_logs_outcome",
        "ix_audit_logs_timestamp",
        "ix_audit_logs_user_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 3. Rename the legacy PK constraint so the new partitioned table
    #    can claim ``audit_logs_pkey``. (PG does NOT rename constraints
    #    when their owning table is renamed, so without this step the
    #    new PK ends up named ``audit_logs_pkey1`` — functional but
    #    ugly to operate on later.)
    op.execute(
        "ALTER TABLE audit_logs "
        "RENAME CONSTRAINT audit_logs_pkey TO audit_logs_legacy_pkey"
    )

    # 4. Rename the existing (unpartitioned) table out of the way.
    op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")

    # 3. Create the new partitioned ``audit_logs`` parent. The PK must
    #    include the partition key; Postgres rejects partitioning a
    #    table whose PK is a non-superset of the partition columns.
    op.execute(
        """
        CREATE TABLE audit_logs (
            id              uuid                     NOT NULL,
            "timestamp"     timestamp with time zone NOT NULL,
            actor           varchar(50)              NOT NULL,
            action          varchar(50)              NOT NULL,
            target_resource varchar(255)             NOT NULL,
            outcome         varchar(50)              NOT NULL,
            audit_metadata  jsonb,
            institution_id  uuid,
            user_id         uuid,
            location_id     uuid,
            PRIMARY KEY (id, "timestamp")
        ) PARTITION BY RANGE ("timestamp")
        """
    )

    # 4. Re-attach indexes at the parent level. PG>=11 propagates
    #    these to every partition (current + future) automatically.
    for column in (
        "action",
        "actor",
        "institution_id",
        "location_id",
        "outcome",
        '"timestamp"',
        "user_id",
    ):
        # Strip quotes for the index name even though we keep them in the
        # column reference (column 'timestamp' is a reserved word).
        index_column_id = column.strip('"')
        op.execute(
            f"CREATE INDEX ix_audit_logs_{index_column_id} "
            f"ON audit_logs ({column})"
        )

    # 5. Re-add CHECK constraint at the parent level.
    op.execute(
        """
        ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_actor_check
        CHECK (actor::text = ANY (ARRAY[
            'RETELL_AGENT'::varchar,
            'ADMIN'::varchar,
            'SYSTEM'::varchar,
            'API_CLIENT'::varchar
        ]::text[]))
        """
    )

    # 6. Enable + force RLS on the parent. Inherits to all partitions.
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")

    # Mirror the policy verbatim from the consolidated baseline so
    # tenant isolation behaviour is byte-identical to before.
    op.execute(
        """
        CREATE POLICY audit_logs_rls ON audit_logs FOR ALL
        USING (
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
        )
        WITH CHECK (
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
        )
        """
    )

    # 7. Re-attach the immutability triggers. PG>=13 lets us define
    #    triggers on a partitioned table, and they fire on every
    #    partition (current + future).
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )

    # 8. Create the initial monthly partitions: previous month +
    #    current + the next ``_INITIAL_FUTURE_MONTHS``. The script
    #    extends this rolling window daily.
    initial_year, initial_month = _add_months(today.year, today.month, -_INITIAL_PAST_MONTHS)
    total_months = _INITIAL_PAST_MONTHS + 1 + _INITIAL_FUTURE_MONTHS
    for offset in range(total_months):
        year, month = _add_months(initial_year, initial_month, offset)
        partition = _partition_name(year, month)
        start, end = _month_bounds(year, month)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {partition} "
            f"PARTITION OF audit_logs "
            f"FOR VALUES FROM ('{start}') TO ('{end}')"
        )

    # 9. DEFAULT partition. Catches any row whose timestamp falls
    #    outside the explicit ranges so an INSERT never fails — but
    #    a metric filter on inserts to this partition signals
    #    "operator: a partition is missing or a clock is skewed".
    op.execute(
        "CREATE TABLE IF NOT EXISTS audit_logs_default "
        "PARTITION OF audit_logs DEFAULT"
    )

    # 10. Copy data from the legacy table to the partitioned one.
    #     For staging this is empty; for any future cutover it
    #     preserves history.
    op.execute("INSERT INTO audit_logs SELECT * FROM audit_logs_legacy")

    # 11. Drop the legacy table (the immutability trigger was already
    #     dropped in step 1, so DROP TABLE works cleanly).
    op.execute("DROP TABLE audit_logs_legacy")

    # 12. Re-grant DML to the runtime role on the parent. PostgreSQL
    #     does NOT inherit table-level GRANTs to partitions; we have
    #     to grant on each partition individually. The
    #     ``ensure_audit_partitions.py`` script also runs the GRANT
    #     when it creates new partitions.
    op.execute(
        """
        DO $$
        DECLARE
            partition_name text;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT ON audit_logs TO nexhealth_app;
                FOR partition_name IN
                    SELECT inhrelid::regclass::text
                    FROM pg_inherits
                    WHERE inhparent = 'audit_logs'::regclass
                LOOP
                    EXECUTE format('GRANT SELECT, INSERT ON %s TO nexhealth_app', partition_name);
                END LOOP;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    """Revert to a non-partitioned audit_logs table.

    Preserves all rows. Useful only for emergency rollback — the
    point of partitioning is the partitioned shape, so a real
    downgrade is operationally rare.
    """
    op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_partitioned_old")

    op.execute(
        """
        CREATE TABLE audit_logs (
            id              uuid                     NOT NULL,
            "timestamp"     timestamp with time zone NOT NULL,
            actor           varchar(50)              NOT NULL,
            action          varchar(50)              NOT NULL,
            target_resource varchar(255)             NOT NULL,
            outcome         varchar(50)              NOT NULL,
            audit_metadata  jsonb,
            institution_id  uuid,
            user_id         uuid,
            location_id     uuid,
            PRIMARY KEY (id)
        )
        """
    )

    op.execute(
        "INSERT INTO audit_logs (id, \"timestamp\", actor, action, target_resource, "
        "outcome, audit_metadata, institution_id, user_id, location_id) "
        "SELECT id, \"timestamp\", actor, action, target_resource, outcome, "
        "audit_metadata, institution_id, user_id, location_id "
        "FROM audit_logs_partitioned_old"
    )
    op.execute("DROP TABLE audit_logs_partitioned_old")

    # The baseline migration's ENABLE/FORCE RLS + CREATE POLICY +
    # CREATE TRIGGER on audit_logs is rerun via ``alembic upgrade``
    # after a downgrade; the policies and triggers re-attach to the
    # new flat table by name.


