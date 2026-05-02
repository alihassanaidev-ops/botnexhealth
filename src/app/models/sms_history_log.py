"""
SMS History Log model for tracking outbound SMS messages and PHI protection.

SOLID Principles Applied:
- SRP: Model only handles data representation and encryption/decryption of PHI
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class SmsStatus(str, Enum):
    """
    Status of the SMS message.
    """
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    DELIVERED = "delivered"


class SmsHistoryLog(Base):
    """
    Audit and tracking log for all outbound SMS messages.
    
    Fields `to_number` and `body` are AES-256-GCM encrypted as they may contain PHI.
    """
    
    __tablename__ = "sms_history_logs"
    
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    
    # When the SMS was initiated (UTC)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    
    # Platform Twilio number (Not PHI)
    from_number: Mapped[str] = mapped_column(String(50), nullable=False)
    
    # PHI fields — AES-256-GCM encrypted at application level
    to_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    to_number_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    
    # Status of the message delivery
    status: Mapped[str] = mapped_column(
        String(50),
        default=SmsStatus.PENDING.value,
        nullable=False,
        index=True
    )
    
    # Twilio SID (if sent successfully)
    message_sid: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    provider_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Error message (if failed) - BE CAREFUL NOT TO LOG RAW PHI HERE
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Relations
    institution_location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    patient_contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    call_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # --- Encrypted field properties ---
    
    @property
    def to_number(self) -> str | None:
        """Get the decrypted recipient phone number."""
        return decrypt_value(self.to_number_encrypted)
    
    @to_number.setter
    def to_number(self, value: str | None) -> None:
        """Set the recipient phone number, encrypting it."""
        if value is not None:
            from src.app.services.sms_privacy import hash_phone, mask_phone

            phone_hash = hash_phone(value)
            if not phone_hash:
                raise ValueError("to_number must be a valid phone number")
            self.to_number_encrypted = encrypt_value(value) # type: ignore
            self.to_number_hash = phone_hash
            self.to_number_masked = mask_phone(value)
        else:
            raise ValueError("to_number cannot be None")
            
    @property
    def body(self) -> str | None:
        """Get the decrypted SMS body."""
        return decrypt_value(self.body_encrypted)
        
    @body.setter
    def body(self, value: str | None) -> None:
        """Set the SMS body, encrypting it."""
        if value is not None:
            self.body_encrypted = encrypt_value(value) # type: ignore
        else:
            raise ValueError("body cannot be None")
            
    def __repr__(self) -> str:
        return (
            f"<SmsHistoryLog("
            f"id={self.id}, "
            f"status={self.status}, "
            f"timestamp={self.timestamp}"
            f")>"
        )
