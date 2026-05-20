"""Add retention metadata for PHI-bearing records.

Revision ID: 20260520_retention_policy
Revises: 20260519_mfa_rls_user_reads
"""

from __future__ import annotations

from alembic import op


revision = "20260520_retention_policy"
down_revision = "20260519_mfa_rls_user_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The consolidated baseline imports live models and may create these
    # columns for fresh databases before this migration runs. Keep every DDL
    # statement idempotent so both fresh and upgraded databases work.
    op.execute(
        """
        ALTER TABLE calls
            ADD COLUMN IF NOT EXISTS retention_class varchar(32) NOT NULL DEFAULT 'clinical_record',
            ADD COLUMN IF NOT EXISTS retain_until timestamptz NOT NULL DEFAULT (now() + interval '10 years'),
            ADD COLUMN IF NOT EXISTS recording_retain_until timestamptz,
            ADD COLUMN IF NOT EXISTS recording_deleted_at timestamptz,
            ADD COLUMN IF NOT EXISTS legal_hold_until timestamptz,
            ADD COLUMN IF NOT EXISTS purged_at timestamptz
        """
    )
    op.execute(
        """
        UPDATE calls
        SET
            retention_class = COALESCE(retention_class, 'clinical_record'),
            retain_until = COALESCE(retain_until, created_at + interval '10 years'),
            recording_retain_until = COALESCE(
                recording_retain_until,
                CASE
                    WHEN recording_url IS NOT NULL THEN created_at + interval '90 days'
                    ELSE NULL
                END
            )
        """
    )
    _create_index("ix_calls_retention_class", "calls", "retention_class")
    _create_index("ix_calls_retain_until", "calls", "retain_until")
    _create_index(
        "ix_calls_recording_retain_until", "calls", "recording_retain_until"
    )
    _create_index("ix_calls_legal_hold_until", "calls", "legal_hold_until")
    _create_index("ix_calls_purged_at", "calls", "purged_at")

    op.execute(
        """
        ALTER TABLE sms_history_logs
            ALTER COLUMN body_encrypted DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS retention_class varchar(32) NOT NULL DEFAULT 'clinical_record',
            ADD COLUMN IF NOT EXISTS retain_until timestamptz NOT NULL DEFAULT (now() + interval '10 years'),
            ADD COLUMN IF NOT EXISTS body_retain_until timestamptz NOT NULL DEFAULT (now() + interval '10 years'),
            ADD COLUMN IF NOT EXISTS body_purged_at timestamptz,
            ADD COLUMN IF NOT EXISTS legal_hold_until timestamptz,
            ADD COLUMN IF NOT EXISTS purged_at timestamptz
        """
    )
    op.execute(
        """
        UPDATE sms_history_logs
        SET
            retention_class = COALESCE(retention_class, 'clinical_record'),
            retain_until = COALESCE(retain_until, "timestamp" + interval '10 years'),
            body_retain_until = COALESCE(body_retain_until, "timestamp" + interval '10 years')
        """
    )
    _create_index(
        "ix_sms_history_logs_retention_class",
        "sms_history_logs",
        "retention_class",
    )
    _create_index("ix_sms_history_logs_retain_until", "sms_history_logs", "retain_until")
    _create_index(
        "ix_sms_history_logs_body_retain_until",
        "sms_history_logs",
        "body_retain_until",
    )
    _create_index("ix_sms_history_logs_body_purged_at", "sms_history_logs", "body_purged_at")
    _create_index(
        "ix_sms_history_logs_legal_hold_until",
        "sms_history_logs",
        "legal_hold_until",
    )
    _create_index("ix_sms_history_logs_purged_at", "sms_history_logs", "purged_at")

    op.execute(
        """
        ALTER TABLE notifications
            ADD COLUMN IF NOT EXISTS retain_until timestamptz NOT NULL DEFAULT (now() + interval '180 days'),
            ADD COLUMN IF NOT EXISTS legal_hold_until timestamptz,
            ADD COLUMN IF NOT EXISTS purged_at timestamptz
        """
    )
    op.execute(
        """
        UPDATE notifications
        SET retain_until = COALESCE(retain_until, created_at + interval '180 days')
        """
    )
    _create_index("ix_notifications_retain_until", "notifications", "retain_until")
    _create_index("ix_notifications_legal_hold_until", "notifications", "legal_hold_until")
    _create_index("ix_notifications_purged_at", "notifications", "purged_at")

    op.execute(
        """
        ALTER TABLE dead_letter_events
            ADD COLUMN IF NOT EXISTS raw_payload_retain_until timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
            ADD COLUMN IF NOT EXISTS raw_payload_purged_at timestamptz
        """
    )
    op.execute(
        """
        UPDATE dead_letter_events
        SET raw_payload_retain_until = COALESCE(
            raw_payload_retain_until,
            created_at + interval '30 days'
        )
        """
    )
    _create_index(
        "ix_dead_letter_events_raw_payload_retain_until",
        "dead_letter_events",
        "raw_payload_retain_until",
    )
    _create_index(
        "ix_dead_letter_events_raw_payload_purged_at",
        "dead_letter_events",
        "raw_payload_purged_at",
    )


def downgrade() -> None:
    for index_name in (
        "ix_dead_letter_events_raw_payload_purged_at",
        "ix_dead_letter_events_raw_payload_retain_until",
        "ix_notifications_purged_at",
        "ix_notifications_legal_hold_until",
        "ix_notifications_retain_until",
        "ix_sms_history_logs_purged_at",
        "ix_sms_history_logs_legal_hold_until",
        "ix_sms_history_logs_body_purged_at",
        "ix_sms_history_logs_body_retain_until",
        "ix_sms_history_logs_retain_until",
        "ix_sms_history_logs_retention_class",
        "ix_calls_purged_at",
        "ix_calls_legal_hold_until",
        "ix_calls_recording_retain_until",
        "ix_calls_retain_until",
        "ix_calls_retention_class",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    op.execute(
        """
        ALTER TABLE dead_letter_events
            DROP COLUMN IF EXISTS raw_payload_purged_at,
            DROP COLUMN IF EXISTS raw_payload_retain_until
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
            DROP COLUMN IF EXISTS purged_at,
            DROP COLUMN IF EXISTS legal_hold_until,
            DROP COLUMN IF EXISTS retain_until
        """
    )
    op.execute(
        """
        ALTER TABLE sms_history_logs
            DROP COLUMN IF EXISTS purged_at,
            DROP COLUMN IF EXISTS legal_hold_until,
            DROP COLUMN IF EXISTS body_purged_at,
            DROP COLUMN IF EXISTS body_retain_until,
            DROP COLUMN IF EXISTS retain_until,
            DROP COLUMN IF EXISTS retention_class
        """
    )
    op.execute(
        """
        ALTER TABLE calls
            DROP COLUMN IF EXISTS purged_at,
            DROP COLUMN IF EXISTS legal_hold_until,
            DROP COLUMN IF EXISTS recording_deleted_at,
            DROP COLUMN IF EXISTS recording_retain_until,
            DROP COLUMN IF EXISTS retain_until,
            DROP COLUMN IF EXISTS retention_class
        """
    )


def _create_index(index_name: str, table_name: str, column_name: str) -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
    )
