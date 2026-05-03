"""Add jurisdiction column to institutions.

Revision ID: 20260503_institution_jurisdiction
Revises: 20260501_sms_phi_hardening
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260503_institution_jurisdiction"
down_revision: Union[str, None] = "20260501_sms_phi_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALLOWED = (
    "CA-ON",
    "CA-BC",
    "CA-AB",
    "CA-QC",
    "CA-MB",
    "CA-SK",
    "CA-NS",
    "CA-NB",
    "CA-NL",
    "CA-PE",
    "CA-YT",
    "CA-NT",
    "CA-NU",
)


def upgrade() -> None:
    op.add_column(
        "institutions",
        sa.Column(
            "jurisdiction",
            sa.String(length=8),
            nullable=False,
            server_default="CA-ON",
        ),
    )
    allowed_list = ", ".join(f"'{code}'" for code in _ALLOWED)
    op.create_check_constraint(
        "ck_institutions_jurisdiction",
        "institutions",
        f"jurisdiction IN ({allowed_list})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_institutions_jurisdiction", "institutions", type_="check")
    op.drop_column("institutions", "jurisdiction")
