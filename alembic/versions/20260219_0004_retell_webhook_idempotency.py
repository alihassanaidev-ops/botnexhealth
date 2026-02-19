"""Add Retell webhook idempotency tracking table.

Revision ID: 20260219_0004
Revises: 20260217_0003
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260219_0004"
down_revision = "20260217_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retell_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("ghl_contact_id", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "event_type", name="uq_retell_webhook_call_event"),
    )
    op.create_index(
        "ix_retell_webhook_events_call_id",
        "retell_webhook_events",
        ["call_id"],
        unique=False,
    )
    op.create_index(
        "ix_retell_webhook_events_event_type",
        "retell_webhook_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_retell_webhook_events_status",
        "retell_webhook_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_retell_webhook_events_tenant_id",
        "retell_webhook_events",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_retell_webhook_events_tenant_id", table_name="retell_webhook_events")
    op.drop_index("ix_retell_webhook_events_status", table_name="retell_webhook_events")
    op.drop_index("ix_retell_webhook_events_event_type", table_name="retell_webhook_events")
    op.drop_index("ix_retell_webhook_events_call_id", table_name="retell_webhook_events")
    op.drop_table("retell_webhook_events")

