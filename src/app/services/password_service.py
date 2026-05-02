"""Password and one-time token helpers for local auth flows."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

class PasswordService:
    """Password hashing, verification, and token helpers."""

    MIN_PASSWORD_LENGTH = 12
    MAX_PASSWORD_BYTES = 256
    TOKEN_BYTES = 32
    _HASHER = PasswordHasher(
        time_cost=3,
        memory_cost=65_536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Validate and hash a password with Argon2id."""
        cls.validate_password_strength(password)
        return cls._HASHER.hash(password)

    @classmethod
    def verify_password(cls, password: str, password_hash: str | None) -> bool:
        """Return True when the password matches the stored hash."""
        if not password or not password_hash:
            return False

        try:
            return bool(cls._HASHER.verify(password_hash, password))
        except (InvalidHashError, TypeError, VerificationError):
            return False

    @classmethod
    def needs_rehash(cls, password_hash: str | None) -> bool:
        """Return True when a stored password hash should be upgraded."""
        if not password_hash:
            return True
        try:
            return cls._HASHER.check_needs_rehash(password_hash)
        except (InvalidHashError, TypeError):
            return True

    @classmethod
    def generate_one_time_token(cls) -> str:
        """Generate a URL-safe token for invite/reset flows."""
        return secrets.token_urlsafe(cls.TOKEN_BYTES)

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a one-time token before persisting it."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def verify_token(cls, token: str, token_hash: str | None) -> bool:
        """Safely compare a plaintext token to a stored hash."""
        if not token or not token_hash:
            return False
        return hmac.compare_digest(cls.hash_token(token), token_hash)

    @classmethod
    def validate_password_strength(cls, password: str) -> None:
        """Raise ValueError when a password does not meet minimum strength."""
        if len(password) < cls.MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {cls.MIN_PASSWORD_LENGTH} characters long."
            )

        if password.isspace():
            raise ValueError("Password cannot be only whitespace.")

        if len(password.encode("utf-8")) > cls.MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be {cls.MAX_PASSWORD_BYTES} bytes or fewer.")

        if not any(ch in string.ascii_lowercase for ch in password):
            raise ValueError("Password must include a lowercase letter.")
        if not any(ch in string.ascii_uppercase for ch in password):
            raise ValueError("Password must include an uppercase letter.")
        if not any(ch in string.digits for ch in password):
            raise ValueError("Password must include a number.")
        if not any(ch in string.punctuation for ch in password):
            raise ValueError("Password must include a symbol.")
