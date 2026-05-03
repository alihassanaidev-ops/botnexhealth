"""Add Retell function-call idempotency tracking table.

Revision ID: 20260503_retell_function_idempotency
Revises: 20260503_institution_jurisdiction
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260503_retell_function_idempotency"
down_revision: Union[str, None] = "20260503_institution_jurisdiction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retell_function_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("function_name", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "call_id",
            "function_name",
            "args_hash",
            name="uq_retell_function_invocation",
        ),
    )
    op.create_index(
        "ix_retell_function_invocations_call_id",
        "retell_function_invocations",
        ["call_id"],
        unique=False,
    )
    op.create_index(
        "ix_retell_function_invocations_function_name",
        "retell_function_invocations",
        ["function_name"],
        unique=False,
    )
    op.create_index(
        "ix_retell_function_invocations_status",
        "retell_function_invocations",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_retell_function_invocations_institution_id",
        "retell_function_invocations",
        ["institution_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retell_function_invocations_institution_id",
        table_name="retell_function_invocations",
    )
    op.drop_index(
        "ix_retell_function_invocations_status",
        table_name="retell_function_invocations",
    )
    op.drop_index(
        "ix_retell_function_invocations_function_name",
        table_name="retell_function_invocations",
    )
    op.drop_index(
        "ix_retell_function_invocations_call_id",
        table_name="retell_function_invocations",
    )
    op.drop_table("retell_function_invocations")
