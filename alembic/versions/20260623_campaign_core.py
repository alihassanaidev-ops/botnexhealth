"""Outbound campaign engine — core state-machine tables.

Phase 0 foundation for the Outbound Engagement Engine
(docs/OUTBOUND_ENGAGEMENT_IMPLEMENTATION_PLAN.md). Adds the four core
orchestration tables; high-volume tables (step_attempts, campaign_events) and
compliance tables (consent / DNC / quiet-hours) + appointment_cache land in
follow-up Phase 0 migrations.

- campaigns            — institution-scoped campaign definition (one per type/loc)
- campaign_versions    — immutable config snapshots (timing/channels/copy/retry)
- sequence_runs        — one per (patient, campaign, trigger); holds FSM state
- sequence_steps       — scheduled steps the poller claims via FOR UPDATE SKIP LOCKED

All tables are institution/location scoped under FORCE RLS (same owned-policy
shape as custom_field_definitions / workflow_statuses). Exactly-once is enforced
at two levels: a unique idempotency key on enrollment (sequence_runs) and the
lease/visibility window on sequence_steps.

Revision ID: 20260623_campaign_core
Revises: 20260622_nopms_call_status
"""

from __future__ import annotations

from alembic import op


revision = "20260623_campaign_core"
down_revision = "20260622_nopms_call_status"
branch_labels = None
depends_on = None


# Institution-owned policy (verbatim shape of the workflow_statuses / custom
# field baseline): super admin, system contexts (the Celery poller/dispatcher
# runs under 'celery'), and the owning institution's users may read/write rows
# for their own institution.
def _owned(table: str) -> str:
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


_TABLES = ("campaigns", "campaign_versions", "sequence_runs", "sequence_steps")


def upgrade() -> None:
    # ── campaigns ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id              uuid PRIMARY KEY,
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id     uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            campaign_type   varchar(40) NOT NULL,
            name            varchar(120) NOT NULL,
            is_active       boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_campaigns_type CHECK (
                campaign_type IN ('confirmation', 'reminder', 'recall', 'sales_qualification')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaigns_institution_type "
        "ON campaigns (institution_id, campaign_type, location_id)"
    )

    # ── campaign_versions (immutable config snapshots) ───────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_versions (
            id              uuid PRIMARY KEY,
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            campaign_id     uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            version         integer NOT NULL,
            config          jsonb NOT NULL,
            is_current      boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_version UNIQUE (campaign_id, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_versions_campaign "
        "ON campaign_versions (campaign_id)"
    )

    # ── sequence_runs (one per patient-campaign-trigger; FSM state) ──────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sequence_runs (
            id                   uuid PRIMARY KEY,
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            campaign_id          uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            campaign_version_id  uuid NOT NULL REFERENCES campaign_versions(id),
            contact_id           uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            -- Source object that triggered enrollment (e.g. cached appointment id);
            -- part of the idempotency key so the same trigger can't enroll twice.
            trigger_ref          varchar(120),
            state                varchar(30) NOT NULL DEFAULT 'pending_enroll',
            idempotency_key      varchar(160) NOT NULL,
            enrolled_at          timestamptz,
            completed_at         timestamptz,
            outcome              varchar(40),
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_sequence_runs_state CHECK (
                state IN ('pending_enroll', 'scheduled', 'due', 'policy_check',
                          'dispatching', 'sent', 'awaiting_response', 'responded',
                          'retry', 'escalate', 'completed', 'failed', 'dead_letter')
            ),
            -- Anti-double-enrollment: one run per (institution, idempotency_key).
            CONSTRAINT uq_sequence_run_idem UNIQUE (institution_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sequence_runs_inst_state "
        "ON sequence_runs (institution_id, state)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sequence_runs_contact "
        "ON sequence_runs (contact_id)"
    )

    # ── sequence_steps (poller claims DUE rows via SKIP LOCKED) ──────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sequence_steps (
            id               uuid PRIMARY KEY,
            institution_id   uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id      uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            sequence_run_id  uuid NOT NULL REFERENCES sequence_runs(id) ON DELETE CASCADE,
            step_order       integer NOT NULL,
            channel          varchar(20) NOT NULL,
            status           varchar(20) NOT NULL DEFAULT 'scheduled',
            due_at           timestamptz NOT NULL,
            -- Visibility/lease window: a claimer sets lease_until = now()+timeout;
            -- another poller skips rows whose lease is still active.
            lease_until      timestamptz,
            attempts         integer NOT NULL DEFAULT 0,
            last_error       text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_sequence_steps_channel CHECK (channel IN ('voice', 'sms', 'email')),
            CONSTRAINT ck_sequence_steps_status CHECK (
                status IN ('scheduled', 'due', 'dispatching', 'sent',
                           'failed', 'skipped', 'dead_letter')
            )
        )
        """
    )
    # Hot poller index: claim "schedulable steps whose time has come", oldest
    # first. Partial index keeps it tiny (only claimable rows) and fast under
    # FOR UPDATE SKIP LOCKED. Per-institution ordering supports sharded fairness.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sequence_steps_claimable "
        "ON sequence_steps (institution_id, due_at) "
        "WHERE status IN ('scheduled', 'due')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sequence_steps_run "
        "ON sequence_steps (sequence_run_id)"
    )

    # ── RLS (FORCE) + owned policy + runtime-role grants ─────────────────
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls ON {table} FOR ALL
            USING ({_owned(table)})
            WITH CHECK ({_owned(table)})
            """
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


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
