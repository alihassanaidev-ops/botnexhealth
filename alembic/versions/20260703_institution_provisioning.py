"""Add per-institution Twilio sub-account and email from-address fields (Plan 10).

Adds nullable encrypted credential columns to the institutions table for
per-institution Twilio sub-accounts and email sending identity.

Revision ID: 20260703_institution_provisioning
Revises: 20260703_consent_channel
"""

from __future__ import annotations

from alembic import op

revision = "20260703_provisioning"
down_revision = "20260703_consent_channel"
branch_labels = None
depends_on = None

# Idempotent (ADD COLUMN IF NOT EXISTS) to match the repo convention: the
# consolidated baseline builds the whole schema from live model metadata via
# Base.metadata.create_all, so on a fresh `upgrade head` these columns already
# exist. Bare op.add_column here previously raised DuplicateColumnError and broke
# fresh deploys; the guard makes this a no-op on fresh DBs and a real add on any
# older DB that predates the model columns.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("twilio_account_sid_encrypted", "TEXT"),
    ("twilio_auth_token_encrypted", "TEXT"),
    ("email_from_address", "VARCHAR(320)"),
    ("email_from_name", "VARCHAR(255)"),
)


def upgrade() -> None:
    for name, coltype in _COLUMNS:
        op.execute(f"ALTER TABLE institutions ADD COLUMN IF NOT EXISTS {name} {coltype}")


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE institutions DROP COLUMN IF EXISTS {name}")
