"""Merge call scrubbed and NexHealth webhook durability heads.

Revision ID: 20260721_merge_call_scrubbed_nexhealth_durability
Revises: 20260720_call_scrubbed, 20260720_nexhealth_webhook_durability
"""

from __future__ import annotations

revision = "20260721_merge_call_scrubbed_nexhealth_durability"
down_revision = (
    "20260720_call_scrubbed",
    "20260720_nexhealth_webhook_durability",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
