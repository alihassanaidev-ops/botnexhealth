"""Durable log of internal status transitions, for the internal_status trigger.

Revision ID: 20260904_internal_status_events
Revises: 20260904_nh_mapping_security
Create Date: 2026-09-04

Campaigns can start when a status the platform owns changes — the workflow
status staff assign to a call, a contact's lead status, or the staff handoff
queue. A session listener writes one row per observed transition inside the same
transaction as the change, and the trigger task re-reads the row by id rather
than trusting its Celery payload.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_internal_status_events"
down_revision = "20260904_nh_mapping_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_status_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "institution_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("institution_locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("field", sa.String(40), nullable=False),
        # Not a foreign key: the referent differs per field (a call, a contact,
        # a handoff), and an event outliving a deleted row is fine.
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(80), nullable=True),
        sa.Column("to_status", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_internal_status_events_institution_id",
        "internal_status_events",
        ["institution_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_internal_status_events_location_id",
        "internal_status_events",
        ["location_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_internal_status_events_contact_id",
        "internal_status_events",
        ["contact_id"],
        if_not_exists=True,
    )
    # The predicate the trigger task issues.
    op.create_index(
        "ix_internal_status_events_match",
        "internal_status_events",
        ["institution_id", "field", "to_status", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_internal_status_events_contact",
        "internal_status_events",
        ["institution_id", "contact_id", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_internal_status_events_contact", "internal_status_events")
    op.drop_index("ix_internal_status_events_match", "internal_status_events")
    op.drop_index("ix_internal_status_events_contact_id", "internal_status_events")
    op.drop_index("ix_internal_status_events_location_id", "internal_status_events")
    op.drop_index("ix_internal_status_events_institution_id", "internal_status_events")
    op.drop_table("internal_status_events")
