"""baseline: stamp existing schema

Revision ID: 0001
Revises: None
Create Date: 2026-02-17

This is a baseline migration. It does NOT create tables — they already exist
in the Supabase database via SQLAlchemy create_all(). This revision simply
marks the starting point so Alembic can track future changes.

To apply: alembic stamp 0001
(This tells Alembic "the database is already at revision 0001" without
running any SQL.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Nothing to do — tables already exist in Supabase."""
    pass


def downgrade() -> None:
    """Nothing to undo — this is a baseline."""
    pass
