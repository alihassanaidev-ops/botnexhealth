"""add_call_tags_callback_note

Adds call_tags (all comma-separated normalized tags from Retell 'Call Status' field)
and callback_note (free-text note when a callback is resolved) to the calls table.

Revision ID: a1b2c3d4e5f6
Revises: c8f2e1a04b37
Create Date: 2026-02-24 00:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "c8f2e1a04b37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("call_tags", sa.Text(), nullable=True))
    op.add_column("calls", sa.Column("callback_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "callback_note")
    op.drop_column("calls", "call_tags")
