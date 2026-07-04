"""Do-not-contact scope tiers (Plan 12 / P7).

Adds a ``scope`` column to ``do_not_contact`` so a DNC can apply to a single
location (the sender that received the STOP), the whole institution (default),
or a DSO group ("remove me everywhere"). Channel-agnostic — a DNC blocks SMS,
voice, and email alike; the compliance gate now enforces it on every channel.

Existing rows predate the column and default to ``institution`` (their prior
institution-wide behavior is preserved exactly).

Idempotent (ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF EXISTS) to match the
repo convention — the consolidated baseline builds the schema from live model
metadata, so on a fresh ``upgrade head`` the column/constraint already exist.

Revision ID: 20260706_dnc_scope
Revises: 20260705_consent_email_identity
"""

from __future__ import annotations

from alembic import op

revision = "20260706_dnc_scope"
down_revision = "20260705_consent_email_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE do_not_contact "
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(32) NOT NULL DEFAULT 'institution'"
    )
    op.execute(
        "ALTER TABLE do_not_contact DROP CONSTRAINT IF EXISTS ck_do_not_contact_scope"
    )
    op.execute(
        "ALTER TABLE do_not_contact "
        "ADD CONSTRAINT ck_do_not_contact_scope "
        "CHECK (scope IN ('location', 'institution', 'group'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE do_not_contact DROP CONSTRAINT IF EXISTS ck_do_not_contact_scope"
    )
    op.execute("ALTER TABLE do_not_contact DROP COLUMN IF EXISTS scope")
