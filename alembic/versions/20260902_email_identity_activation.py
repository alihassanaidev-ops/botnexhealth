"""Separate SES verification from live sending activation.

Revision ID: 20260902_email_identity_activation
Revises: 20260902_contact_rls
"""

from alembic import op
import sqlalchemy as sa


revision = "20260902_email_identity_activation"
down_revision = "20260902_contact_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_sending_identities",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("email_sending_identities", "is_active")
