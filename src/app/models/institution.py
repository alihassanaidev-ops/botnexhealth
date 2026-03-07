"""Institution model for multi-institution architecture."""

from __future__ import annotations

import base64
import secrets
from datetime import datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


# =============================================================================
# AES-256-GCM Encryption (HIPAA Compliant)
# =============================================================================
# - AES-256: 256-bit key (32 bytes), NIST approved
# - GCM mode: Authenticated encryption (integrity + confidentiality)
# - Random 96-bit IV: Unique per encryption
# - Format: base64(iv + ciphertext + tag)
# =============================================================================

def _get_encryption_key() -> bytes:
    """Get 32-byte AES-256 encryption key from Settings.

    Uses the Settings object (which reads from .env, env vars, and Docker secrets)
    rather than os.getenv() directly. os.getenv() does NOT read .env files —
    pydantic-settings does, but only into the Settings instance.
    """
    from src.app.config import get_settings

    key_b64 = get_settings().encryption_key
    if not key_b64:
        raise RuntimeError("ENCRYPTION_KEY not set in environment or .env file")

    key = base64.urlsafe_b64decode(key_b64)
    if len(key) != 32:
        raise RuntimeError(
            f"ENCRYPTION_KEY must be 32 bytes (256 bits) for AES-256. "
            f"Got {len(key)} bytes. Generate with: "
            f"python -c \"import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
        )
    return key


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


def decrypt_value(value: str | None) -> str | None:
    """
    Decrypt a string value encrypted with AES-256-GCM.

    Expects base64-encoded string containing IV + ciphertext + auth tag.
    """
    if value is None:
        return None

    key = _get_encryption_key()
    aesgcm = AESGCM(key)

    # Decode base64
    encrypted_data = base64.urlsafe_b64decode(value)

    # Extract IV (first 12 bytes) and ciphertext+tag (rest)
    iv = encrypted_data[:12]
    ciphertext = encrypted_data[12:]

    # Decrypt (GCM verifies auth tag automatically)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)

    return plaintext.decode("utf-8")


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
