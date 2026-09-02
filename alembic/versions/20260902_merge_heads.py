"""Merge email inbox with the staging qualification/form integration head.

Revision ID: 20260902_merge_heads
Revises: 20260902_complete_email_inbox, 20260902_merge_qual_forms
"""

revision = "20260902_merge_heads"
down_revision = (
    "20260902_complete_email_inbox",
    "20260902_merge_qual_forms",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
