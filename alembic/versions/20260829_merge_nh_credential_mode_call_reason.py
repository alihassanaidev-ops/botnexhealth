"""Merge the NexHealth credential and call-detail migration branches."""

from __future__ import annotations


revision = "20260829_merge_nh_credential_mode_call_reason"
down_revision = ("20260821_nh_credential_mode", "20260829_call_disconnection_reason")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema work; this revision only joins the migration branches."""


def downgrade() -> None:
    """No schema work; downgrade re-exposes the two parent heads."""
