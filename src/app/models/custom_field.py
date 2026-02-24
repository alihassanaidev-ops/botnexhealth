"""Custom field models for tenant-defined fields on Contacts and Calls.

HIPAA Compliance:
- Fields marked is_phi=True have values stored AES-256-GCM encrypted.
- Non-PHI values stored plaintext for queryability (workflow automation).
- All records are tenant-scoped (tenant_id NOT NULL).

EAV Pattern:
- CustomFieldDefinition: what fields exist per tenant + entity type
- CustomFieldValue: actual values per contact/call row
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.tenant import decrypt_value, encrypt_value


class EntityType(str, Enum):
    """Which entity a custom field applies to."""

    CONTACT = "contact"
    CALL = "call"


class FieldType(str, Enum):
    """Data type of a custom field value."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DROPDOWN = "dropdown"


class RetellSource(str, Enum):
    """Which Retell webhook dict a custom field value is sourced from."""

    CUSTOM_ANALYSIS_DATA = "custom_analysis_data"
    COLLECTED_DYNAMIC_VARIABLES = "collected_dynamic_variables"


class CustomFieldDefinition(Base):
    """
    Defines a custom field that a tenant has created.

    Tenants can create custom fields for contacts or calls,
    choosing the field type, whether it contains PHI, and
    optionally providing dropdown options.
    """

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "field_key",
            name="uq_custom_field_tenant_entity_key",
        ),
        Index("ix_custom_field_def_tenant_entity", "tenant_id", "entity_type"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Which entity this field applies to (contact or call)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Display name shown in the UI (e.g. "Referral Source")
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Slug key for programmatic access (e.g. "referral_source")
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)

    # Data type
    field_type: Mapped[str] = mapped_column(
        String(20), default=FieldType.TEXT.value, nullable=False,
    )

    # Options for dropdown fields (JSON array of strings)
    dropdown_options: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # HIPAA: if True, values are AES-256-GCM encrypted
    is_phi: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # UI ordering
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Retell webhook auto-population: which dict and key to pull from
    retell_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    retell_source_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Soft delete
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CustomFieldDefinition(id={self.id}, "
            f"entity={self.entity_type}, key='{self.field_key}', "
            f"type={self.field_type}, phi={self.is_phi})>"
        )


class CustomFieldValue(Base):
    """
    Stores the value of a custom field for a specific contact or call.

    HIPAA: When the linked definition has is_phi=True, the value is
    stored in value_encrypted (AES-256-GCM) and value_text is NULL.
    """

    __tablename__ = "custom_field_values"
    __table_args__ = (
        UniqueConstraint(
            "field_definition_id", "entity_id",
            name="uq_custom_field_value_def_entity",
        ),
        Index("ix_custom_field_val_tenant_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    field_definition_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which entity type and specific record this value belongs to
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)

    # Non-PHI value (plaintext for queryability / workflow filters)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PHI value (AES-256-GCM encrypted)
    value_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # =========================================================================
    # Value access — routes to encrypted or plaintext based on definition
    # =========================================================================

    def get_value(self, is_phi: bool = False) -> str | None:
        """Get the field value, decrypting if PHI."""
        if is_phi:
            return decrypt_value(self.value_encrypted)
        return self.value_text

    def set_value(self, value: str | None, is_phi: bool = False) -> None:
        """Set the field value, encrypting if PHI."""
        if is_phi:
            self.value_encrypted = encrypt_value(value)
            self.value_text = None
        else:
            self.value_text = value
            self.value_encrypted = None

    def __repr__(self) -> str:
        return (
            f"<CustomFieldValue(id={self.id}, "
            f"entity={self.entity_type}:{self.entity_id}, "
            f"field={self.field_definition_id})>"
        )
