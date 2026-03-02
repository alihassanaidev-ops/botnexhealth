"""Rename Tenant to Institution + add LOCATION role

Revision ID: 20260301_rename_tenant_to_institution
Revises: 20260228_contact_pms_identity
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260301_rename_tenant_to_institution"
down_revision = "20260228_contact_pms_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =====================================================================
    # 1. Rename tables
    # =====================================================================
    op.rename_table("tenants", "institutions")
    op.rename_table("tenant_locations", "institution_locations")
    op.rename_table("tenant_providers", "institution_providers")
    op.rename_table("tenant_appointment_types", "institution_appointment_types")
    op.rename_table("tenant_descriptors", "institution_descriptors")
    op.rename_table("tenant_operatories", "institution_operatories")

    # =====================================================================
    # 2. Rename tenant_id → institution_id columns
    # =====================================================================

    # -- institution_locations --
    op.drop_constraint("tenant_locations_tenant_id_fkey", "institution_locations", type_="foreignkey")
    op.alter_column("institution_locations", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "institution_locations_institution_id_fkey",
        "institution_locations", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    # Rename index
    op.execute("ALTER INDEX IF EXISTS ix_tenant_locations_tenant_id RENAME TO ix_institution_locations_institution_id")

    # -- institution_providers --
    op.drop_constraint("tenant_providers_tenant_id_fkey", "institution_providers", type_="foreignkey")
    op.drop_constraint("tenant_providers_location_id_fkey", "institution_providers", type_="foreignkey")
    op.alter_column("institution_providers", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "institution_providers_institution_id_fkey",
        "institution_providers", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "institution_providers_location_id_fkey",
        "institution_providers", "institution_locations",
        ["location_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_tenant_providers_tenant_id RENAME TO ix_institution_providers_institution_id")

    # -- institution_appointment_types --
    op.drop_constraint("tenant_appointment_types_tenant_id_fkey", "institution_appointment_types", type_="foreignkey")
    op.drop_constraint("tenant_appointment_types_location_id_fkey", "institution_appointment_types", type_="foreignkey")
    op.alter_column("institution_appointment_types", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "institution_appointment_types_institution_id_fkey",
        "institution_appointment_types", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "institution_appointment_types_location_id_fkey",
        "institution_appointment_types", "institution_locations",
        ["location_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_tenant_appointment_types_tenant_id RENAME TO ix_institution_appointment_types_institution_id")

    # -- institution_descriptors --
    op.drop_constraint("tenant_descriptors_tenant_id_fkey", "institution_descriptors", type_="foreignkey")
    op.drop_constraint("tenant_descriptors_location_id_fkey", "institution_descriptors", type_="foreignkey")
    op.alter_column("institution_descriptors", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "institution_descriptors_institution_id_fkey",
        "institution_descriptors", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "institution_descriptors_location_id_fkey",
        "institution_descriptors", "institution_locations",
        ["location_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_tenant_descriptors_tenant_id RENAME TO ix_institution_descriptors_institution_id")

    # -- institution_operatories --
    op.drop_constraint("tenant_operatories_tenant_id_fkey", "institution_operatories", type_="foreignkey")
    op.drop_constraint("tenant_operatories_location_id_fkey", "institution_operatories", type_="foreignkey")
    op.alter_column("institution_operatories", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "institution_operatories_institution_id_fkey",
        "institution_operatories", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "institution_operatories_location_id_fkey",
        "institution_operatories", "institution_locations",
        ["location_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_tenant_operatories_tenant_id RENAME TO ix_institution_operatories_institution_id")

    # -- users --
    op.drop_constraint("users_tenant_id_fkey", "users", type_="foreignkey")
    op.alter_column("users", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "users_institution_id_fkey",
        "users", "institutions",
        ["institution_id"], ["id"],
    )
    op.execute("ALTER INDEX IF EXISTS ix_users_tenant_id RENAME TO ix_users_institution_id")

    # Add location_id column to users
    op.add_column("users", sa.Column("location_id", UUID(as_uuid=False), nullable=True))
    op.create_index("ix_users_location_id", "users", ["location_id"])
    op.create_foreign_key(
        "users_location_id_fkey",
        "users", "institution_locations",
        ["location_id"], ["id"],
    )

    # -- calls --
    op.drop_constraint("calls_tenant_id_fkey", "calls", type_="foreignkey")
    op.alter_column("calls", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "calls_institution_id_fkey",
        "calls", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    # Rename call indexes
    op.execute("ALTER INDEX IF EXISTS ix_call_tenant RENAME TO ix_call_institution")
    op.execute("ALTER INDEX IF EXISTS ix_call_tenant_status RENAME TO ix_call_institution_status")
    op.execute("ALTER INDEX IF EXISTS ix_call_tenant_date RENAME TO ix_call_institution_date")
    op.execute("ALTER INDEX IF EXISTS ix_call_tenant_contact RENAME TO ix_call_institution_contact")

    # -- contacts --
    op.drop_constraint("contacts_tenant_id_fkey", "contacts", type_="foreignkey")
    op.alter_column("contacts", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "contacts_institution_id_fkey",
        "contacts", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    # Rename contact indexes and constraints
    op.execute("ALTER INDEX IF EXISTS ix_contact_tenant RENAME TO ix_contact_institution")
    op.execute("ALTER INDEX IF EXISTS ix_contact_tenant_nexhealth RENAME TO ix_contact_institution_nexhealth")
    op.execute("ALTER INDEX IF EXISTS ix_contact_tenant_phone RENAME TO ix_contact_institution_phone")
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS uq_contact_tenant_pms")
    op.create_unique_constraint("uq_contact_institution_pms", "contacts", ["institution_id", "nexhealth_patient_id"])

    # -- custom_field_definitions --
    op.drop_constraint("custom_field_definitions_tenant_id_fkey", "custom_field_definitions", type_="foreignkey")
    op.alter_column("custom_field_definitions", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "custom_field_definitions_institution_id_fkey",
        "custom_field_definitions", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    # Rename constraint and index
    op.execute("ALTER TABLE custom_field_definitions DROP CONSTRAINT IF EXISTS uq_custom_field_tenant_entity_key")
    op.create_unique_constraint(
        "uq_custom_field_institution_entity_key",
        "custom_field_definitions",
        ["institution_id", "entity_type", "field_key"],
    )
    op.execute("ALTER INDEX IF EXISTS ix_custom_field_def_tenant_entity RENAME TO ix_custom_field_def_institution_entity")

    # -- custom_field_values --
    op.drop_constraint("custom_field_values_tenant_id_fkey", "custom_field_values", type_="foreignkey")
    op.alter_column("custom_field_values", "tenant_id", new_column_name="institution_id")
    op.create_foreign_key(
        "custom_field_values_institution_id_fkey",
        "custom_field_values", "institutions",
        ["institution_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_custom_field_val_tenant_entity RENAME TO ix_custom_field_val_institution_entity")

    # -- audit_logs (tenant_id column — no FK, just rename) --
    op.alter_column("audit_logs", "tenant_id", new_column_name="institution_id")
    op.execute("ALTER INDEX IF EXISTS ix_audit_logs_tenant_id RENAME TO ix_audit_logs_institution_id")

    # -- retell_webhook_events (tenant_id column — no FK, just rename) --
    op.alter_column("retell_webhook_events", "tenant_id", new_column_name="institution_id")
    op.execute("ALTER INDEX IF EXISTS ix_retell_webhook_events_tenant_id RENAME TO ix_retell_webhook_events_institution_id")

    # =====================================================================
    # 3. Update role values: TENANT → INSTITUTION
    # =====================================================================
    op.execute("UPDATE users SET role = 'INSTITUTION' WHERE role = 'TENANT'")

    # =====================================================================
    # 4. Update audit log actions
    # =====================================================================
    op.execute("UPDATE audit_logs SET action = 'INSTITUTION_CREATE' WHERE action = 'TENANT_CREATE'")
    op.execute("UPDATE audit_logs SET action = 'INSTITUTION_UPDATE' WHERE action = 'TENANT_UPDATE'")
    op.execute("UPDATE audit_logs SET action = 'INSTITUTION_DELETE' WHERE action = 'TENANT_DELETE'")


def downgrade() -> None:
    # =====================================================================
    # Reverse audit log actions
    # =====================================================================
    op.execute("UPDATE audit_logs SET action = 'TENANT_CREATE' WHERE action = 'INSTITUTION_CREATE'")
    op.execute("UPDATE audit_logs SET action = 'TENANT_UPDATE' WHERE action = 'INSTITUTION_UPDATE'")
    op.execute("UPDATE audit_logs SET action = 'TENANT_DELETE' WHERE action = 'INSTITUTION_DELETE'")

    # =====================================================================
    # Reverse role values
    # =====================================================================
    op.execute("UPDATE users SET role = 'TENANT' WHERE role = 'INSTITUTION'")

    # =====================================================================
    # Reverse column renames (institution_id → tenant_id)
    # =====================================================================

    # -- retell_webhook_events --
    op.alter_column("retell_webhook_events", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_retell_webhook_events_institution_id RENAME TO ix_retell_webhook_events_tenant_id")

    # -- audit_logs --
    op.alter_column("audit_logs", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_audit_logs_institution_id RENAME TO ix_audit_logs_tenant_id")

    # -- custom_field_values --
    op.drop_constraint("custom_field_values_institution_id_fkey", "custom_field_values", type_="foreignkey")
    op.alter_column("custom_field_values", "institution_id", new_column_name="tenant_id")
    op.create_foreign_key(
        "custom_field_values_tenant_id_fkey",
        "custom_field_values", "institutions",
        ["tenant_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_custom_field_val_institution_entity RENAME TO ix_custom_field_val_tenant_entity")

    # -- custom_field_definitions --
    op.execute("ALTER TABLE custom_field_definitions DROP CONSTRAINT IF EXISTS uq_custom_field_institution_entity_key")
    op.drop_constraint("custom_field_definitions_institution_id_fkey", "custom_field_definitions", type_="foreignkey")
    op.alter_column("custom_field_definitions", "institution_id", new_column_name="tenant_id")
    op.create_foreign_key(
        "custom_field_definitions_tenant_id_fkey",
        "custom_field_definitions", "institutions",
        ["tenant_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_custom_field_tenant_entity_key",
        "custom_field_definitions",
        ["tenant_id", "entity_type", "field_key"],
    )
    op.execute("ALTER INDEX IF EXISTS ix_custom_field_def_institution_entity RENAME TO ix_custom_field_def_tenant_entity")

    # -- contacts --
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS uq_contact_institution_pms")
    op.drop_constraint("contacts_institution_id_fkey", "contacts", type_="foreignkey")
    op.alter_column("contacts", "institution_id", new_column_name="tenant_id")
    op.create_foreign_key(
        "contacts_tenant_id_fkey",
        "contacts", "institutions",
        ["tenant_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_contact_tenant_pms", "contacts", ["tenant_id", "nexhealth_patient_id"])
    op.execute("ALTER INDEX IF EXISTS ix_contact_institution RENAME TO ix_contact_tenant")
    op.execute("ALTER INDEX IF EXISTS ix_contact_institution_nexhealth RENAME TO ix_contact_tenant_nexhealth")
    op.execute("ALTER INDEX IF EXISTS ix_contact_institution_phone RENAME TO ix_contact_tenant_phone")

    # -- calls --
    op.drop_constraint("calls_institution_id_fkey", "calls", type_="foreignkey")
    op.alter_column("calls", "institution_id", new_column_name="tenant_id")
    op.create_foreign_key(
        "calls_tenant_id_fkey",
        "calls", "institutions",
        ["tenant_id"], ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER INDEX IF EXISTS ix_call_institution RENAME TO ix_call_tenant")
    op.execute("ALTER INDEX IF EXISTS ix_call_institution_status RENAME TO ix_call_tenant_status")
    op.execute("ALTER INDEX IF EXISTS ix_call_institution_date RENAME TO ix_call_tenant_date")
    op.execute("ALTER INDEX IF EXISTS ix_call_institution_contact RENAME TO ix_call_tenant_contact")

    # -- users --
    op.drop_constraint("users_location_id_fkey", "users", type_="foreignkey")
    op.drop_index("ix_users_location_id", "users")
    op.drop_column("users", "location_id")
    op.drop_constraint("users_institution_id_fkey", "users", type_="foreignkey")
    op.alter_column("users", "institution_id", new_column_name="tenant_id")
    op.create_foreign_key(
        "users_tenant_id_fkey",
        "users", "institutions",
        ["tenant_id"], ["id"],
    )
    op.execute("ALTER INDEX IF EXISTS ix_users_institution_id RENAME TO ix_users_tenant_id")

    # -- institution_operatories → tenant_operatories --
    op.drop_constraint("institution_operatories_institution_id_fkey", "institution_operatories", type_="foreignkey")
    op.drop_constraint("institution_operatories_location_id_fkey", "institution_operatories", type_="foreignkey")
    op.alter_column("institution_operatories", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_institution_operatories_institution_id RENAME TO ix_tenant_operatories_tenant_id")

    # -- institution_descriptors → tenant_descriptors --
    op.drop_constraint("institution_descriptors_institution_id_fkey", "institution_descriptors", type_="foreignkey")
    op.drop_constraint("institution_descriptors_location_id_fkey", "institution_descriptors", type_="foreignkey")
    op.alter_column("institution_descriptors", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_institution_descriptors_institution_id RENAME TO ix_tenant_descriptors_tenant_id")

    # -- institution_appointment_types → tenant_appointment_types --
    op.drop_constraint("institution_appointment_types_institution_id_fkey", "institution_appointment_types", type_="foreignkey")
    op.drop_constraint("institution_appointment_types_location_id_fkey", "institution_appointment_types", type_="foreignkey")
    op.alter_column("institution_appointment_types", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_institution_appointment_types_institution_id RENAME TO ix_tenant_appointment_types_tenant_id")

    # -- institution_providers → tenant_providers --
    op.drop_constraint("institution_providers_institution_id_fkey", "institution_providers", type_="foreignkey")
    op.drop_constraint("institution_providers_location_id_fkey", "institution_providers", type_="foreignkey")
    op.alter_column("institution_providers", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_institution_providers_institution_id RENAME TO ix_tenant_providers_tenant_id")

    # -- institution_locations → tenant_locations --
    op.drop_constraint("institution_locations_institution_id_fkey", "institution_locations", type_="foreignkey")
    op.alter_column("institution_locations", "institution_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX IF EXISTS ix_institution_locations_institution_id RENAME TO ix_tenant_locations_tenant_id")

    # =====================================================================
    # Rename tables back
    # =====================================================================
    op.rename_table("institution_operatories", "tenant_operatories")
    op.rename_table("institution_descriptors", "tenant_descriptors")
    op.rename_table("institution_appointment_types", "tenant_appointment_types")
    op.rename_table("institution_providers", "tenant_providers")
    op.rename_table("institution_locations", "tenant_locations")
    op.rename_table("institutions", "tenants")

    # Recreate FKs with old names on renamed tables
    op.create_foreign_key("tenant_locations_tenant_id_fkey", "tenant_locations", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_providers_tenant_id_fkey", "tenant_providers", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_providers_location_id_fkey", "tenant_providers", "tenant_locations", ["location_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_appointment_types_tenant_id_fkey", "tenant_appointment_types", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_appointment_types_location_id_fkey", "tenant_appointment_types", "tenant_locations", ["location_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_descriptors_tenant_id_fkey", "tenant_descriptors", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_descriptors_location_id_fkey", "tenant_descriptors", "tenant_locations", ["location_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_operatories_tenant_id_fkey", "tenant_operatories", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("tenant_operatories_location_id_fkey", "tenant_operatories", "tenant_locations", ["location_id"], ["id"], ondelete="CASCADE")
