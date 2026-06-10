"""Add per-tenant retention override columns to institutions.

Revision ID: 20260610_retention_overrides
Revises: 20260521_contact_anonymization
"""

from __future__ import annotations

from alembic import op


revision = "20260610_retention_overrides"
down_revision = "20260521_contact_anonymization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE institutions
            ADD COLUMN IF NOT EXISTS retention_clinical_record_days integer
        """
    )
    op.execute(
        """
        ALTER TABLE institutions
            ADD COLUMN IF NOT EXISTS retention_recording_days integer
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE institutions DROP COLUMN IF EXISTS retention_recording_days")
    op.execute("ALTER TABLE institutions DROP COLUMN IF EXISTS retention_clinical_record_days")
