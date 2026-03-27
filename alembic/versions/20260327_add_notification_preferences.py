"""Add external notification recipients and user email notification preferences.

Revision ID: 20260327_notification_prefs
Revises: 20260325_email_templates
Create Date: 2026-03-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260327_notification_prefs"
down_revision: Union[str, None] = "20260325_email_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- External notification recipients --
    op.create_table(
        "external_notification_recipients",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "institution_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("template_type", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_ext_recipient_institution",
        "external_notification_recipients",
        ["institution_id"],
    )

    op.create_index(
        "ix_ext_recipient_institution_email_type",
        "external_notification_recipients",
        ["institution_id", "email", "template_type"],
        unique=True,
    )

    # -- User email notification preferences --
    op.create_table(
        "user_email_notification_preferences",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("template_type", sa.String(50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_user_email_pref_user",
        "user_email_notification_preferences",
        ["user_id"],
    )

    op.create_index(
        "ix_user_email_pref_user_type",
        "user_email_notification_preferences",
        ["user_id", "template_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_email_pref_user_type", table_name="user_email_notification_preferences")
    op.drop_index("ix_user_email_pref_user", table_name="user_email_notification_preferences")
    op.drop_table("user_email_notification_preferences")

    op.drop_index("ix_ext_recipient_institution_email_type", table_name="external_notification_recipients")
    op.drop_index("ix_ext_recipient_institution", table_name="external_notification_recipients")
    op.drop_table("external_notification_recipients")
