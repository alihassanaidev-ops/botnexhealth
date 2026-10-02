"""Expand ConsentChannel to include email and voice (Plan 12 Slice 2).

Drops the SMS-only check constraints on consent_records and
sms_suppressions and replaces them with constraints that also allow
'email' and 'voice'. Existing rows (all 'sms') are unaffected.

Revision ID: 20260703_consent_channel
Revises: 20260703_outbound_halt
"""

from __future__ import annotations

from alembic import op

revision = "20260703_consent_channel"
down_revision = "20260703_outbound_halt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE consent_records "
        "DROP CONSTRAINT IF EXISTS ck_consent_records_channel"
    )
    op.execute(
        "ALTER TABLE consent_records "
        "ADD CONSTRAINT ck_consent_records_channel "
        "CHECK (channel IN ('sms', 'email', 'voice'))"
    )

    op.execute(
        "ALTER TABLE sms_suppressions "
        "DROP CONSTRAINT IF EXISTS ck_sms_suppressions_channel"
    )
    op.execute(
        "ALTER TABLE sms_suppressions "
        "ADD CONSTRAINT ck_sms_suppressions_channel "
        "CHECK (channel IN ('sms', 'email', 'voice'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE consent_records "
        "DROP CONSTRAINT IF EXISTS ck_consent_records_channel"
    )
    op.execute(
        "ALTER TABLE consent_records "
        "ADD CONSTRAINT ck_consent_records_channel "
        "CHECK (channel IN ('sms'))"
    )

    op.execute(
        "ALTER TABLE sms_suppressions "
        "DROP CONSTRAINT IF EXISTS ck_sms_suppressions_channel"
    )
    op.execute(
        "ALTER TABLE sms_suppressions "
        "ADD CONSTRAINT ck_sms_suppressions_channel "
        "CHECK (channel IN ('sms'))"
    )
