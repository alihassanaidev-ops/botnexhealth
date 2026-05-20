"""Add contact anonymization marker for retention.

Revision ID: 20260521_contact_anonymization
Revises: 20260520_retention_policy
"""

from __future__ import annotations

from alembic import op


revision = "20260521_contact_anonymization"
down_revision = "20260520_retention_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the consolidated baseline may create this column for fresh
    # databases from the live model before this migration runs.
    op.execute(
        """
        ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS anonymized_at timestamptz
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_contacts_anonymized_at
            ON contacts (anonymized_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contacts_anonymized_at")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS anonymized_at")
