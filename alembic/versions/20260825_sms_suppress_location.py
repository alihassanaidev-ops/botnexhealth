"""Scope SMS suppressions to the receiving location.

Revision ID: 20260825_sms_suppress_location
Revises: 20260825_twilio_sms_rls
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260825_sms_suppress_location"
down_revision = "20260825_twilio_sms_rls"
branch_labels = None
depends_on = None


OLD_LOOKUP_INDEX = "ix_sms_suppressions_institution_phone_active"
NEW_LOOKUP_INDEX = "ix_sms_suppressions_institution_location_phone_active"
OLD_UNIQUE_INDEX = "uq_sms_suppressions_active_institution_channel_phone"
NEW_UNIQUE_INDEX = "uq_sms_suppressions_active_location_channel_phone"


def upgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {OLD_LOOKUP_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {OLD_UNIQUE_INDEX}")
    op.create_index(
        NEW_LOOKUP_INDEX,
        "sms_suppressions",
        ["institution_id", "location_id", "phone_hash", "is_active"],
    )
    op.create_index(
        NEW_UNIQUE_INDEX,
        "sms_suppressions",
        ["institution_id", "location_id", "channel", "phone_hash"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index(NEW_UNIQUE_INDEX, table_name="sms_suppressions")
    op.drop_index(NEW_LOOKUP_INDEX, table_name="sms_suppressions")
    op.create_index(
        OLD_LOOKUP_INDEX,
        "sms_suppressions",
        ["institution_id", "phone_hash", "is_active"],
    )
    op.create_index(
        OLD_UNIQUE_INDEX,
        "sms_suppressions",
        ["institution_id", "channel", "phone_hash"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
