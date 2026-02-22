"""Contact model for storing patient/caller records.

HIPAA Compliance:
- Email, phone, and DOB are AES-256-GCM encrypted at application level.
- phone_hash enables caller-ID lookups without decrypting every row.
- Names stored plaintext for clinic staff dashboard reference (per HIPAA scope doc §3.2).
- All records are tenant-scoped (tenant_id NOT NULL).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database import Base
from src.app.models.tenant import decrypt_value, encrypt_value


class Contact(Base):
    """
    A patient or caller associated with a tenant (clinic).

    One row per unique caller per tenant. Linked to Call records.
    """

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_hash", name="uq_contact_tenant_phone"),
        Index("ix_contact_tenant", "tenant_id"),
        Index("ix_contact_tenant_nexhealth", "tenant_id", "nexhealth_patient_id"),
    )

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    # Tenant isolation (NOT NULL — every contact belongs to exactly one clinic)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Identity fields (plaintext — clinic staff reference)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # PHI fields — AES-256-GCM encrypted at application level
    email_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_of_birth_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phone hash for caller-ID lookups (SHA-256, not reversible)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # PMS link
    nexhealth_patient_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Interaction tracking
    chat_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_agent_interaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_new_patient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
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

    # Relationships
    calls: Mapped[list["Call"]] = relationship(  # noqa: F821
        "Call", back_populates="contact", lazy="selectin",
    )

    # =========================================================================
    # Encrypted field properties
    # =========================================================================

    @property
    def email(self) -> str | None:
        return decrypt_value(self.email_encrypted)

    @email.setter
    def email(self, value: str | None) -> None:
        self.email_encrypted = encrypt_value(value)

    @property
    def phone(self) -> str | None:
        return decrypt_value(self.phone_encrypted)

    @phone.setter
    def phone(self, value: str | None) -> None:
        self.phone_encrypted = encrypt_value(value)
        # Update hash for lookups
        self.phone_hash = self._hash_phone(value) if value else None

    @property
    def date_of_birth(self) -> str | None:
        return decrypt_value(self.date_of_birth_encrypted)

    @date_of_birth.setter
    def date_of_birth(self, value: str | None) -> None:
        self.date_of_birth_encrypted = encrypt_value(value)

    @staticmethod
    def _hash_phone(phone: str) -> str:
        """SHA-256 hash of phone number for lookup without decryption."""
        # Normalize: strip spaces, dashes, parens — keep only digits and +
        normalized = "".join(c for c in phone if c.isdigit() or c == "+")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def find_by_phone_hash(cls, phone: str) -> str:
        """Generate the hash to use in a WHERE clause for phone lookup."""
        return cls._hash_phone(phone)

    def __repr__(self) -> str:
        return f"<Contact(id={self.id}, tenant_id={self.tenant_id}, name='{self.full_name}')>"
