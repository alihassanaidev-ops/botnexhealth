"""Make institution_locations.slug unique per-institution, not globally.

Revision ID: 20260507_location_slug_per_inst
Revises: 20260506_rls_full_staged
Create Date: 2026-05-07

Two different institutions cannot both have a location named "main"
under a globally-unique constraint — that's wrong for a multi-tenant
platform. Replace the global UNIQUE on slug with a composite UNIQUE
on (institution_id, slug).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260507_location_slug_per_inst"
down_revision: Union[str, None] = "20260506_rls_full_staged"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_institution_locations_slug", table_name="institution_locations")
    op.create_index(
        "ix_institution_locations_slug",
        "institution_locations",
        ["slug"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_institution_locations_inst_slug",
        "institution_locations",
        ["institution_id", "slug"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_institution_locations_inst_slug",
        "institution_locations",
        type_="unique",
    )
    op.drop_index("ix_institution_locations_slug", table_name="institution_locations")
    op.create_index(
        "ix_institution_locations_slug",
        "institution_locations",
        ["slug"],
        unique=True,
    )
