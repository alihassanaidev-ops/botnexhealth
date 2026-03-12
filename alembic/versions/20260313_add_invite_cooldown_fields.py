"""Add invite cooldown fields to users.

Revision ID: 20260313_invite_cooldown
Revises: 20260312_billing_email
Create Date: 2026-03-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260313_invite_cooldown"
down_revision: Union[str, None] = "20260312_billing_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invite_cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "invite_cooldown_exponent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_invite_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_invite_at")
    op.drop_column("users", "invite_cooldown_exponent")
    op.drop_column("users", "invite_cooldown_until")
