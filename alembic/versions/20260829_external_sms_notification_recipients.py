"""Add no-PMS external SMS notification recipients.

Revision ID: 20260829_external_sms_recipients
Revises: 20260829_merge_nh_credential_mode_call_reason
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260829_external_sms_recipients"
down_revision = "20260829_merge_nh_credential_mode_call_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_sms_notification_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("phone_number_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_number_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_number_masked", sa.String(length=32), nullable=False),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_external_sms_notification_recipients_institution_id",
        "external_sms_notification_recipients",
        ["institution_id"],
    )
    op.create_index(
        "ix_external_sms_notification_recipients_phone_number_hash",
        "external_sms_notification_recipients",
        ["phone_number_hash"],
    )
    op.create_index(
        "ix_ext_sms_recipient_institution_phone_type",
        "external_sms_notification_recipients",
        ["institution_id", "phone_number_hash", "notification_type"],
        unique=True,
    )
    op.execute("ALTER TABLE external_sms_notification_recipients ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_sms_notification_recipients FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY external_sms_notification_recipients_rls
        ON external_sms_notification_recipients
        FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
                AND external_sms_notification_recipients.institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND external_sms_notification_recipients.institution_id = app_rls_institution_id()
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
                AND external_sms_notification_recipients.institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND external_sms_notification_recipients.institution_id = app_rls_institution_id()
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS external_sms_notification_recipients_rls ON external_sms_notification_recipients")
    op.drop_index(
        "ix_ext_sms_recipient_institution_phone_type",
        table_name="external_sms_notification_recipients",
    )
    op.drop_index(
        "ix_external_sms_notification_recipients_phone_number_hash",
        table_name="external_sms_notification_recipients",
    )
    op.drop_index(
        "ix_external_sms_notification_recipients_institution_id",
        table_name="external_sms_notification_recipients",
    )
    op.drop_table("external_sms_notification_recipients")
