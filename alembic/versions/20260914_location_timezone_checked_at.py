"""When a location's timezone was last compared against its practice software.

NexHealth reports a location's zone on its location record, but the appointment
webhook carries none, so the value has to be pulled. Pulling it on every
sync-status sweep would double that sweep's NexHealth traffic, and background
traffic shares an allowance of only 60 requests/minute per API key with
reconciliation and backfill.

This column paces the pull instead: at most once a day per location, restart-safe
because it is stored rather than held in memory. NULL means "never checked",
which is why a newly configured location is checked on the very next sweep
rather than waiting out a window it was never part of.

Revision ID: 20260914_loc_tz_checked
Revises: 20260910_location_roi
"""

from __future__ import annotations

from alembic import op


revision = "20260914_loc_tz_checked"
down_revision = "20260910_location_roi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations "
        "ADD COLUMN IF NOT EXISTS timezone_checked_at timestamptz"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations DROP COLUMN timezone_checked_at"
    )
