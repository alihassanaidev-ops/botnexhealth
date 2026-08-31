"""Scope staff SMS recipients to a location, matching staff email recipients.

Adds a nullable ``location_id`` to ``external_sms_notification_recipients``:
NULL means the recipient receives alerts for every location in the institution
(the behaviour every existing row keeps), while a set value restricts them to
calls at that location. Mirrors ``resolve_staff_recipients`` on the email side,
where location admins and staff only hear about their own site.

The unique key has to become two partial indexes: Postgres treats NULLs as
distinct, so a single index spanning a nullable ``location_id`` would allow the
same institution-wide number to be inserted repeatedly.

Revision ID: 20260830_sms_recipient_location_scope
Revises: 20260829_external_sms_recipients
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260830_sms_recipient_location_scope"
down_revision = "20260829_external_sms_recipients"
branch_labels = None
depends_on = None

_TABLE = "external_sms_notification_recipients"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("location_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_ext_sms_recipient_location",
        _TABLE,
        "institution_locations",
        ["location_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_external_sms_notification_recipients_location_id",
        _TABLE,
        ["location_id"],
    )

    # Replace the location-blind unique key with one index per scope.
    op.drop_index("ix_ext_sms_recipient_institution_phone_type", table_name=_TABLE)
    op.create_index(
        "ix_ext_sms_recipient_institution_phone_type",
        _TABLE,
        ["institution_id", "location_id", "phone_number_hash", "notification_type"],
        unique=True,
        postgresql_where=sa.text("location_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ext_sms_recipient_institution_phone_type_all_locs",
        _TABLE,
        ["institution_id", "phone_number_hash", "notification_type"],
        unique=True,
        postgresql_where=sa.text("location_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_ext_sms_recipient_institution_phone_type_all_locs", table_name=_TABLE)
    op.drop_index("ix_ext_sms_recipient_institution_phone_type", table_name=_TABLE)
    op.create_index(
        "ix_ext_sms_recipient_institution_phone_type",
        _TABLE,
        ["institution_id", "phone_number_hash", "notification_type"],
        unique=True,
    )
    op.drop_index("ix_external_sms_notification_recipients_location_id", table_name=_TABLE)
    op.drop_constraint("fk_ext_sms_recipient_location", _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "location_id")
