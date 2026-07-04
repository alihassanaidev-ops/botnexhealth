"""Consent legal basis on consent_records (Plan 03 / Plan 12 V-3).

Adds ``basis`` (express_written / express / implied / exempt_treatment) so the
compliance gate can require an express basis for marketing-class voice/email while
allowing implied/exempt for care reminders. NULL = legacy/unspecified (the gate
interprets it as "implied").

Idempotent (ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF EXISTS) to match the repo
convention — the consolidated baseline builds the schema from live model metadata.

Revision ID: 20260707_consent_basis
Revises: 20260706_dnc_scope
"""

from __future__ import annotations

from alembic import op

revision = "20260707_consent_basis"
down_revision = "20260706_dnc_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE consent_records ADD COLUMN IF NOT EXISTS basis VARCHAR(32)")
    op.execute("ALTER TABLE consent_records DROP CONSTRAINT IF EXISTS ck_consent_records_basis")
    op.execute(
        "ALTER TABLE consent_records "
        "ADD CONSTRAINT ck_consent_records_basis "
        "CHECK (basis IS NULL OR basis IN ('express_written', 'express', 'implied', 'exempt_treatment'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE consent_records DROP CONSTRAINT IF EXISTS ck_consent_records_basis")
    op.execute("ALTER TABLE consent_records DROP COLUMN IF EXISTS basis")
