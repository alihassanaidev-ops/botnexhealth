"""add_account_lockout_fields

Adds failed_login_attempts and locked_until to the users table for
HIPAA §164.312(d) account lockout enforcement.

Revision ID: c8f2e1a04b37
Revises: 4135a8bffe53
Create Date: 2026-02-23 00:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c8f2e1a04b37"
down_revision: Union[str, None] = "4135a8bffe53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
