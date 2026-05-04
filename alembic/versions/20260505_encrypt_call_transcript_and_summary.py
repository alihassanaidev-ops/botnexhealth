"""Encrypt call transcript and summary; drop raw transcript columns.

Revision ID: 20260505_encrypt_call_transcript
Revises: 20260504_enum_check_constraints
Create Date: 2026-05-05

Raw, unredacted transcript fields are removed because we never persist
unredacted PHI from Retell. The remaining (scrubbed) structured transcript
and the summary are stored AES-256-GCM encrypted at the application layer
on top of RDS at-rest encryption.

This migration is destructive — it drops the legacy ``transcript``,
``transcript_with_tool_calls``, ``scrubbed_transcript_with_tool_calls`` and
``summary`` columns. There is no production data at the time of writing, so
no backfill is performed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260505_encrypt_call_transcript"
down_revision: Union[str, None] = "20260504_enum_check_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("transcript_with_tool_calls_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("summary_encrypted", sa.Text(), nullable=True),
    )

    op.drop_column("calls", "transcript")
    op.drop_column("calls", "transcript_with_tool_calls")
    op.drop_column("calls", "scrubbed_transcript_with_tool_calls")
    op.drop_column("calls", "summary")


def downgrade() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    op.add_column("calls", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "calls",
        sa.Column("scrubbed_transcript_with_tool_calls", JSONB(), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("transcript_with_tool_calls", JSONB(), nullable=True),
    )
    op.add_column("calls", sa.Column("transcript", sa.Text(), nullable=True))

    op.drop_column("calls", "summary_encrypted")
    op.drop_column("calls", "transcript_with_tool_calls_encrypted")
