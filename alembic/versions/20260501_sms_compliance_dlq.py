"""Add SMS compliance and dead-letter tables.

Revision ID: 20260501_sms_compliance_dlq
Revises: 20260423_audit_user_soft_delete
Create Date: 2026-05-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260501_sms_compliance_dlq"
down_revision: Union[str, None] = "20260423_audit_user_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sms_history_logs", sa.Column("to_number_hash", sa.String(length=64), nullable=True))
    op.add_column("sms_history_logs", sa.Column("to_number_masked", sa.String(length=32), nullable=True))
    op.add_column("sms_history_logs", sa.Column("provider_status", sa.String(length=50), nullable=True))
    op.add_column("sms_history_logs", sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sms_history_logs_to_number_hash", "sms_history_logs", ["to_number_hash"])
    op.create_index("ix_sms_history_logs_message_sid", "sms_history_logs", ["message_sid"])

    op.create_table(
        "consent_records",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("institution_id", UUID(as_uuid=False), nullable=False),
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
        sa.Column("contact_id", UUID(as_uuid=False), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_masked", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["institution_locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consent_records_institution_id", "consent_records", ["institution_id"])
    op.create_index("ix_consent_records_location_id", "consent_records", ["location_id"])
    op.create_index("ix_consent_records_contact_id", "consent_records", ["contact_id"])
    op.create_index("ix_consent_records_channel", "consent_records", ["channel"])
    op.create_index("ix_consent_records_phone_hash", "consent_records", ["phone_hash"])
    op.create_index("ix_consent_records_status", "consent_records", ["status"])
    op.create_index("ix_consent_records_created_by_user_id", "consent_records", ["created_by_user_id"])
    op.create_index("ix_consent_records_created_at", "consent_records", ["created_at"])
    op.create_index(
        "ix_consent_records_institution_channel_phone",
        "consent_records",
        ["institution_id", "channel", "phone_hash"],
    )

    op.create_table(
        "sms_suppressions",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("institution_id", UUID(as_uuid=False), nullable=False),
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
        sa.Column("contact_id", UUID(as_uuid=False), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_masked", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("keyword", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("released_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["institution_locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sms_suppressions_institution_id", "sms_suppressions", ["institution_id"])
    op.create_index("ix_sms_suppressions_location_id", "sms_suppressions", ["location_id"])
    op.create_index("ix_sms_suppressions_contact_id", "sms_suppressions", ["contact_id"])
    op.create_index("ix_sms_suppressions_channel", "sms_suppressions", ["channel"])
    op.create_index("ix_sms_suppressions_phone_hash", "sms_suppressions", ["phone_hash"])
    op.create_index("ix_sms_suppressions_is_active", "sms_suppressions", ["is_active"])
    op.create_index("ix_sms_suppressions_created_by_user_id", "sms_suppressions", ["created_by_user_id"])
    op.create_index("ix_sms_suppressions_released_by_user_id", "sms_suppressions", ["released_by_user_id"])
    op.create_index("ix_sms_suppressions_created_at", "sms_suppressions", ["created_at"])
    op.create_index(
        "ix_sms_suppressions_institution_phone_active",
        "sms_suppressions",
        ["institution_id", "phone_hash", "is_active"],
    )

    op.create_table(
        "do_not_contact",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("institution_id", UUID(as_uuid=False), nullable=False),
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
        sa.Column("contact_id", UUID(as_uuid=False), nullable=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_masked", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("released_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["institution_locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_do_not_contact_institution_id", "do_not_contact", ["institution_id"])
    op.create_index("ix_do_not_contact_location_id", "do_not_contact", ["location_id"])
    op.create_index("ix_do_not_contact_contact_id", "do_not_contact", ["contact_id"])
    op.create_index("ix_do_not_contact_phone_hash", "do_not_contact", ["phone_hash"])
    op.create_index("ix_do_not_contact_is_active", "do_not_contact", ["is_active"])
    op.create_index("ix_do_not_contact_created_by_user_id", "do_not_contact", ["created_by_user_id"])
    op.create_index("ix_do_not_contact_released_by_user_id", "do_not_contact", ["released_by_user_id"])
    op.create_index("ix_do_not_contact_created_at", "do_not_contact", ["created_at"])
    op.create_index(
        "ix_do_not_contact_institution_phone_active",
        "do_not_contact",
        ["institution_id", "phone_hash", "is_active"],
    )

    op.create_table(
        "dead_letter_events",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("redacted_payload", sa.JSON(), nullable=True),
        sa.Column("raw_payload_encrypted", sa.Text(), nullable=True),
        sa.Column("institution_id", UUID(as_uuid=False), nullable=True),
        sa.Column("location_id", UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["location_id"], ["institution_locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letter_events_source", "dead_letter_events", ["source"])
    op.create_index("ix_dead_letter_events_event_type", "dead_letter_events", ["event_type"])
    op.create_index("ix_dead_letter_events_status", "dead_letter_events", ["status"])
    op.create_index("ix_dead_letter_events_payload_hash", "dead_letter_events", ["payload_hash"])
    op.create_index("ix_dead_letter_events_institution_id", "dead_letter_events", ["institution_id"])
    op.create_index("ix_dead_letter_events_location_id", "dead_letter_events", ["location_id"])
    op.create_index("ix_dead_letter_events_created_at", "dead_letter_events", ["created_at"])
    op.create_index("ix_dead_letter_events_resolved_by_user_id", "dead_letter_events", ["resolved_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_dead_letter_events_resolved_by_user_id", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_created_at", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_location_id", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_institution_id", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_payload_hash", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_status", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_event_type", table_name="dead_letter_events")
    op.drop_index("ix_dead_letter_events_source", table_name="dead_letter_events")
    op.drop_table("dead_letter_events")

    op.drop_index("ix_do_not_contact_institution_phone_active", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_created_at", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_released_by_user_id", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_created_by_user_id", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_is_active", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_phone_hash", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_contact_id", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_location_id", table_name="do_not_contact")
    op.drop_index("ix_do_not_contact_institution_id", table_name="do_not_contact")
    op.drop_table("do_not_contact")

    op.drop_index("ix_sms_suppressions_institution_phone_active", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_created_at", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_released_by_user_id", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_created_by_user_id", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_is_active", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_phone_hash", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_channel", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_contact_id", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_location_id", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_institution_id", table_name="sms_suppressions")
    op.drop_table("sms_suppressions")

    op.drop_index("ix_consent_records_institution_channel_phone", table_name="consent_records")
    op.drop_index("ix_consent_records_created_at", table_name="consent_records")
    op.drop_index("ix_consent_records_created_by_user_id", table_name="consent_records")
    op.drop_index("ix_consent_records_status", table_name="consent_records")
    op.drop_index("ix_consent_records_phone_hash", table_name="consent_records")
    op.drop_index("ix_consent_records_channel", table_name="consent_records")
    op.drop_index("ix_consent_records_contact_id", table_name="consent_records")
    op.drop_index("ix_consent_records_location_id", table_name="consent_records")
    op.drop_index("ix_consent_records_institution_id", table_name="consent_records")
    op.drop_table("consent_records")

    op.drop_index("ix_sms_history_logs_message_sid", table_name="sms_history_logs")
    op.drop_index("ix_sms_history_logs_to_number_hash", table_name="sms_history_logs")
    op.drop_column("sms_history_logs", "last_status_at")
    op.drop_column("sms_history_logs", "provider_status")
    op.drop_column("sms_history_logs", "to_number_masked")
    op.drop_column("sms_history_logs", "to_number_hash")
