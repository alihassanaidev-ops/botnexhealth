"""contact identity from phone to pms patient id

Revision ID: 20260228_contact_pms_identity
Revises: 20260228_twilio_from_number
Create Date: 2026-02-28
"""

from alembic import op

revision = "20260228_contact_pms_identity"
down_revision = "20260228_twilio_from_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop old phone-based uniqueness (allows multiple contacts per phone)
    op.drop_constraint("uq_contact_tenant_phone", "contacts", type_="unique")

    # 2. Add PMS-based uniqueness (one contact per NexHealth patient per tenant)
    op.create_unique_constraint(
        "uq_contact_tenant_pms", "contacts", ["tenant_id", "nexhealth_patient_id"],
    )

    # 3. Non-unique index for phone lookups (replaces the dropped unique constraint)
    op.create_index(
        "ix_contact_tenant_phone", "contacts", ["tenant_id", "phone_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_tenant_phone", table_name="contacts")
    op.drop_constraint("uq_contact_tenant_pms", "contacts", type_="unique")
    op.create_unique_constraint(
        "uq_contact_tenant_phone", "contacts", ["tenant_id", "phone_hash"],
    )
