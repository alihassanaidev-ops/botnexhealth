"""Add invite_status column to users table.

Revision ID: 20260309_invite_status
Revises: 20260308_super_admin_rbac
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260309_invite_status"
down_revision: Union[str, None] = "20260308_super_admin_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invite_status", sa.String(20), nullable=False, server_default="ACCEPTED"),
    )
    # Existing users have already accepted (they can log in).
    # New invites will be created with PENDING explicitly.


def downgrade() -> None:
    op.drop_column("users", "invite_status")
