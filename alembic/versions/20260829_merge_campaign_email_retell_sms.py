"""Merge the campaign-email and Retell-SMS migration branches.

The campaign-email and Retell-SMS branches both descended from
``20260824_remove_reply_keys``. This revision records their convergence so
Alembic has one deployable head after both feature sets are installed.
"""

from __future__ import annotations


revision = "20260829_merge_campaign_email_retell_sms"
down_revision = ("20260825_inbound_email", "20260828_retell_sms_cancel")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema work; this revision only joins the two migration branches."""


def downgrade() -> None:
    """No schema work; downgrade re-exposes the two parent heads."""
