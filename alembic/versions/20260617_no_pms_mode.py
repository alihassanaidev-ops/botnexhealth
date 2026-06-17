"""No-PMS institutions: pms_type mode + contact identity/merge columns.

Adds:
- institutions.pms_type ("nexhealth" default | "none") — call-intelligence-only tenants.
- contacts.merged_into_id (self-FK) — manual, reversible identity linking.
- ix_contact_institution_phone_name — supports no-PMS phone+name auto-match.

Revision ID: 20260617_no_pms_mode
Revises: 20260610_retention_overrides
"""

from __future__ import annotations

from alembic import op


revision = "20260617_no_pms_mode"
down_revision = "20260610_retention_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Institution PMS mode. Existing rows backfill to 'nexhealth' via the default.
    op.execute(
        """
        ALTER TABLE institutions
            ADD COLUMN IF NOT EXISTS pms_type varchar(20) NOT NULL DEFAULT 'nexhealth'
        """
    )

    # Manual contact identity linking (alias -> primary). SET NULL on primary delete.
    op.execute(
        """
        ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS merged_into_id uuid
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_contact_merged_into'
            ) THEN
                ALTER TABLE contacts
                    ADD CONSTRAINT fk_contact_merged_into
                    FOREIGN KEY (merged_into_id) REFERENCES contacts(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contacts_merged_into_id ON contacts (merged_into_id)"
    )

    # No-PMS phone+name auto-match lookup support.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_contact_institution_phone_name
            ON contacts (institution_id, phone_hash, full_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contact_institution_phone_name")
    op.execute("DROP INDEX IF EXISTS ix_contacts_merged_into_id")
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS fk_contact_merged_into")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS merged_into_id")
    op.execute("ALTER TABLE institutions DROP COLUMN IF EXISTS pms_type")
