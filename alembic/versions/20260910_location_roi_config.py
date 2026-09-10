"""Per-location ROI inputs.

The value calculator's inputs were institution-wide, so a group with a downtown
practice and a suburban one had to pick a single average appointment value for
both. These are genuinely per-location economics; the subscription cost is not,
and deliberately stays on the institution.

Revision ID: 20260910_location_roi
Revises: 20260908_nh_lookup_rls
"""

from __future__ import annotations

from alembic import op


revision = "20260910_location_roi"
down_revision = "20260908_nh_lookup_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no default: NULL means "this location has not set its own
    # numbers", which is what makes the institution fall-through visible rather
    # than indistinguishable from a location that happens to match.
    op.execute(
        "ALTER TABLE institution_locations "
        "ADD COLUMN IF NOT EXISTS roi_config jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE institution_locations DROP COLUMN roi_config")
