"""Per-location recall campaign configuration.

The recall scanner could only filter recall rows through a workflow trigger
filter, which cannot express "these exact clinic-approved recall type ids", and
the live booking agent had no per-location constraint at all. This JSONB column
holds the clinic-approved recall types, the due-date window, the recall-type to
appointment-type mapping, and the allowed providers.

NULL means "not configured": the scanner keeps its legacy behavior and no
booking guard is applied. A location that sets this opts into enforced recall
audiences and constrained booking.

Revision ID: 20261005_loc_recall_cfg
Revises: 20260914_loc_tz_checked
"""

from __future__ import annotations

from alembic import op


revision = "20261005_loc_recall_cfg"
down_revision = "20260914_loc_tz_checked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations "
        "ADD COLUMN IF NOT EXISTS recall_config jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE institution_locations DROP COLUMN recall_config")
