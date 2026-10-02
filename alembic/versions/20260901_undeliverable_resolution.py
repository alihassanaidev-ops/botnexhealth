"""Record why an undeliverable event was dismissed.

Revision ID: 20260901_undeliv_resolution
Revises: 20260901_lead_on_contact
"""

from __future__ import annotations

from alembic import op

revision = "20260901_undeliv_resolution"
down_revision = "20260901_lead_on_contact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dead_letter_events "
        "ADD COLUMN IF NOT EXISTS resolution_reason varchar(64)"
    )
    op.execute(
        "ALTER TABLE dead_letter_events "
        "ADD COLUMN IF NOT EXISTS resolution_note_encrypted text"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE dead_letter_events "
        "DROP COLUMN IF EXISTS resolution_note_encrypted"
    )
    op.execute(
        "ALTER TABLE dead_letter_events "
        "DROP COLUMN IF EXISTS resolution_reason"
    )
