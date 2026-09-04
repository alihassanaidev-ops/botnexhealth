"""Per-campaign cron schedules, claimed like workflow timers.

Revision ID: 20260904_workflow_schedules
Revises: 20260904_internal_status_events
Create Date: 2026-09-04

Celery beat here is static, hardcoded, and embedded in a worker pinned to one
instance, so a campaign's schedule cannot be a beat entry. One row per
(workflow, location) carries the cron, the zone it is read in, and a cursor; a
single fixed-interval beat task claims the due ones.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_workflow_schedules"
down_revision = "20260904_internal_status_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_schedules",
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
            sa.ForeignKey("institution_locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("automation_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_version_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("automation_workflow_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cron", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "location_id",
            name="uq_workflow_schedules_workflow_location",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_workflow_schedules_institution_id", "workflow_schedules", ["institution_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_workflow_schedules_location_id", "workflow_schedules", ["location_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_workflow_schedules_workflow_id", "workflow_schedules", ["workflow_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_workflow_schedules_next_fire_at", "workflow_schedules", ["next_fire_at"],
        if_not_exists=True,
    )
    # The claim predicate.
    op.create_index(
        "ix_workflow_schedules_due", "workflow_schedules", ["is_active", "next_fire_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_workflow_schedules_institution",
        "workflow_schedules",
        ["institution_id", "is_active"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_schedules_institution", "workflow_schedules")
    op.drop_index("ix_workflow_schedules_due", "workflow_schedules")
    op.drop_index("ix_workflow_schedules_next_fire_at", "workflow_schedules")
    op.drop_index("ix_workflow_schedules_workflow_id", "workflow_schedules")
    op.drop_index("ix_workflow_schedules_location_id", "workflow_schedules")
    op.drop_index("ix_workflow_schedules_institution_id", "workflow_schedules")
    op.drop_table("workflow_schedules")
