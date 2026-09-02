"""Merge email inbox, form integrations, and qualification metrics heads.

Revision ID: 20260902_merge_heads
Revises: 20260902_complete_email_inbox, 20260902_form_integrations,
    20260902_campaign_qual_metrics
"""

revision = "20260902_merge_heads"
down_revision = (
    "20260902_complete_email_inbox",
    "20260902_form_integrations",
    "20260902_campaign_qual_metrics",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
