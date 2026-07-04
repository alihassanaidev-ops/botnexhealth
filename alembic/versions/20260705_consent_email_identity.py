"""Email consent identity on consent_records (Plan 12 / P0-2).

Adds an email-based consent identity so the EMAIL channel is keyed on the
contact's email address instead of a phone hash. Email-only contacts (no phone)
previously could never pass the email consent gate ("no_phone"). This:

- adds ``email_hash`` (keyed hash of the normalized address) + ``email_masked``,
- relaxes ``phone_hash`` / ``phone_masked`` to NULLable (each channel carries
  only the identity it needs — SMS/VOICE=phone, EMAIL=email),
- adds a lookup index on (institution_id, channel, email_hash).

Backward compatible: existing sms rows keep their phone_hash/phone_masked.

Idempotent (ADD COLUMN IF NOT EXISTS / DROP ... IF EXISTS) to match the repo
convention — the consolidated baseline builds the schema from live model
metadata (Base.metadata.create_all), so on a fresh ``upgrade head`` these
columns already exist and bare op.add_column would raise DuplicateColumnError.

Revision ID: 20260705_consent_email_identity
Revises: 20260703_retell_from_number
"""

from __future__ import annotations

from alembic import op

revision = "20260705_consent_email_identity"
down_revision = "20260703_retell_from_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE consent_records "
        "ADD COLUMN IF NOT EXISTS email_hash VARCHAR(64)"
    )
    op.execute(
        "ALTER TABLE consent_records "
        "ADD COLUMN IF NOT EXISTS email_masked VARCHAR(320)"
    )
    op.execute("ALTER TABLE consent_records ALTER COLUMN phone_hash DROP NOT NULL")
    op.execute("ALTER TABLE consent_records ALTER COLUMN phone_masked DROP NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_consent_records_institution_channel_email "
        "ON consent_records (institution_id, channel, email_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_consent_records_institution_channel_email")
    op.execute("ALTER TABLE consent_records DROP COLUMN IF EXISTS email_masked")
    op.execute("ALTER TABLE consent_records DROP COLUMN IF EXISTS email_hash")
    # phone_* NOT NULL is intentionally left relaxed on downgrade: re-adding it
    # would fail if any email-only rows were written while the column was nullable.
