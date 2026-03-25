"""Add email_templates table for customizable notification emails.

Revision ID: 20260325_email_templates
Revises: 20260313_transfer_numbers
Create Date: 2026-03-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260325_email_templates"
down_revision: Union[str, None] = "20260313_transfer_numbers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "institution_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("template_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("subject_template", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
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
        "ix_email_template_institution_type",
        "email_templates",
        ["institution_id", "template_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_email_template_institution_type", table_name="email_templates")
    op.drop_table("email_templates")
