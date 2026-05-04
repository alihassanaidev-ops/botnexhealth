"""Institution model for multi-institution architecture."""

from __future__ import annotations

import base64
import enum
import secrets
from binascii import Error as BinasciiError
from datetime import datetime
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.security import derive_secret_key


class Jurisdiction(str, enum.Enum):
    """Regulatory jurisdiction governing an institution's PHI handling.

    ISO 3166-2:CA codes — drives data-residency controls, breach-notification
    windows, and provincial privacy-law application (PHIPA, PIPA, Law 25, etc.)
    """

    CA_ON = "CA-ON"  # Ontario — PHIPA
    CA_BC = "CA-BC"  # British Columbia — PIPA BC
    CA_AB = "CA-AB"  # Alberta — HIA
    CA_QC = "CA-QC"  # Quebec — Law 25
    CA_MB = "CA-MB"  # Manitoba — PHIA
    CA_SK = "CA-SK"  # Saskatchewan — HIPA
    CA_NS = "CA-NS"  # Nova Scotia — PHIA
    CA_NB = "CA-NB"  # New Brunswick — PHIPAA
    CA_NL = "CA-NL"  # Newfoundland & Labrador — PHIA
    CA_PE = "CA-PE"  # Prince Edward Island — HIA
    CA_YT = "CA-YT"  # Yukon
    CA_NT = "CA-NT"  # Northwest Territories
    CA_NU = "CA-NU"  # Nunavut


DEFAULT_JURISDICTION = Jurisdiction.CA_ON


# =============================================================================
# AES-256-GCM Encryption (HIPAA Compliant)
# =============================================================================
# - AES-256: 256-bit key (32 bytes), NIST approved
# - GCM mode: Authenticated encryption (integrity + confidentiality)
# - Random 96-bit IV: Unique per encryption
# - Format: base64(iv + ciphertext + tag)
# =============================================================================

def _get_encryption_key() -> bytes:
    """Get a stable 32-byte AES-256 encryption key from Settings."""
    from src.app.config import get_settings

    key_material = get_settings().encryption_key
    if not key_material:
        raise RuntimeError("ENCRYPTION_KEY not set in environment or .env file")

    decoded_key = _decode_base64_key(key_material)
    if decoded_key and len(decoded_key) == 32:
        return decoded_key

    raw_key = key_material.encode("utf-8")
    if len(raw_key) == 32:
        return raw_key

    return derive_secret_key(
        purpose="aes-encryption-key-v1",
        secret=key_material,
        length=32,
    )


def _decode_base64_key(key_material: str) -> bytes | None:
    padded = key_material + ("=" * (-len(key_material) % 4))
    try:
        return base64.urlsafe_b64decode(padded)
    except (BinasciiError, ValueError):
        return None


def encrypt_value(value: str | None) -> str | None:
    """
    Encrypt a string value using AES-256-GCM.

    Returns base64-encoded string containing IV + ciphertext + auth tag.
    """
    if value is None:
        return None

    key = _get_encryption_key()
    aesgcm = AESGCM(key)

    # 96-bit (12 byte) IV as recommended for GCM
    iv = secrets.token_bytes(12)

    # Encrypt (GCM automatically appends 16-byte auth tag)
    ciphertext = aesgcm.encrypt(iv, value.encode("utf-8"), None)

    # Combine: iv (12) + ciphertext + tag (16)
    encrypted_data = iv + ciphertext

    return base64.urlsafe_b64encode(encrypted_data).decode("ascii")


class DecryptionError(RuntimeError):
    """Raised when an encrypted PHI value cannot be decrypted.

    Surfacing this as a typed error (rather than letting binascii.Error or
    cryptography.exceptions.InvalidTag bubble up as a 500) lets the route
    layer audit FAILURE_INTERNAL and return a clean message — and signals
    to operators that data corruption / wrong-key / partial-write may have
    happened.
    """


def decrypt_value(value: str | None) -> str | None:
    """
    Decrypt a string value encrypted with AES-256-GCM.

    Expects base64-encoded string containing IV + ciphertext + auth tag.
    Raises DecryptionError on corruption / wrong key / malformed input.
    """
    if value is None:
        return None

    key = _get_encryption_key()
    aesgcm = AESGCM(key)

    try:
        encrypted_data = base64.urlsafe_b64decode(value)
        iv = encrypted_data[:12]
        ciphertext = encrypted_data[12:]
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
        return plaintext.decode("utf-8")
    except Exception as e:
        # Don't include the ciphertext or key in logs. Hash the ciphertext
        # so operators can correlate corruption events without exposing PHI.
        import hashlib
        import logging
        logger = logging.getLogger(__name__)
        cipher_fp = hashlib.sha256(value.encode("ascii", errors="replace")).hexdigest()[:16]
        logger.critical(
            "PHI decryption failed: type=%s cipher_fp=%s — possible corruption, "
            "wrong ENCRYPTION_KEY, or partial write",
            type(e).__name__, cipher_fp,
        )
        raise DecryptionError(
            f"Failed to decrypt value (cipher_fp={cipher_fp})"
        ) from e


class Institution(Base):
    """
    Institution model storing per-client configuration and credentials.

    All API keys/secrets are stored encrypted.
    """

    __tablename__ = "institutions"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    # Institution identifiers
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    location_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")

    # Regulatory jurisdiction (ISO 3166-2:CA). Drives residency-of-record and
    # provincial privacy-law application; see Jurisdiction enum above.
    jurisdiction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=DEFAULT_JURISDICTION.value,
        server_default=DEFAULT_JURISDICTION.value,
    )

    # ROI configuration (institution-configurable)
    roi_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Billing email for invoices
    billing_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # NexHealth credentials (encrypted)
    nexhealth_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # =========================================================================
    # Encrypted field properties
    # =========================================================================

    @property
    def nexhealth_api_key(self) -> str | None:
        """Decrypt and return NexHealth API key."""
        return decrypt_value(self.nexhealth_api_key_encrypted)

    @nexhealth_api_key.setter
    def nexhealth_api_key(self, value: str | None) -> None:
        """Encrypt and store NexHealth API key."""
        self.nexhealth_api_key_encrypted = encrypt_value(value)

    def __repr__(self) -> str:
        return f"<Institution(id={self.id}, name='{self.name}', slug='{self.slug}')>"
