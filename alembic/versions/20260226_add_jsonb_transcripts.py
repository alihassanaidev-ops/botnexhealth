"""add_jsonb_transcripts

Adds two JSONB columns to the calls table to store structured transcript data
from Retell's webhook:
  - transcript_with_tool_calls: full unredacted turn-by-turn array (with tool calls)
  - scrubbed_transcript_with_tool_calls: PII-scrubbed turn-by-turn array (HIPAA-friendly default)

The existing `transcript` text column is kept unchanged for data continuity.

Revision ID: e1f2a3b4c5d6
Revises: b2c3d4e5f6a7
Create Date: 2026-02-26 00:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op


# revision identifiers
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("transcript_with_tool_calls", JSONB(), nullable=True))
    op.add_column("calls", sa.Column("scrubbed_transcript_with_tool_calls", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "scrubbed_transcript_with_tool_calls")
    op.drop_column("calls", "transcript_with_tool_calls")
