"""Add notifications table for in-app notification system.

Revision ID: 20260310_notifications
Revises: 20260309_provider_cutoff
Create Date: 2026-03-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "20260310_notifications"
down_revision: Union[str, None] = "20260309_provider_cutoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "institution_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("data", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Index for unread-count polling: WHERE user_id = ? AND is_read = false
    op.create_index(
        "ix_notification_user_unread",
        "notifications",
        ["user_id", "is_read"],
    )

    # Index for paginated list: ORDER BY created_at DESC WHERE user_id = ?
    op.create_index(
        "ix_notification_user_created",
        "notifications",
        ["user_id", "created_at"],
    )

    # Index for institution-level bulk operations
    op.create_index(
        "ix_notification_institution",
        "notifications",
        ["institution_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_institution", table_name="notifications")
    op.drop_index("ix_notification_user_created", table_name="notifications")
    op.drop_index("ix_notification_user_unread", table_name="notifications")
    op.drop_table("notifications")
