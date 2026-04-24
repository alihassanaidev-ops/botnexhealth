"""Password and one-time token helpers for local auth flows."""

from __future__ import annotations

import bcrypt
import hashlib
import hmac
import secrets

_COMMON_PASSWORDS = {
    "12345678",
    "password",
    "password1",
    "password123",
    "qwerty123",
    "letmein",
    "admin123",
}


class PasswordService:
    """Password hashing, verification, and token helpers."""

    MIN_PASSWORD_LENGTH = 8
    TOKEN_BYTES = 32

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Validate and hash a password with bcrypt."""
        cls.validate_password_strength(password)
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str | None) -> bool:
        """Return True when the password matches the stored hash."""
        if not password or not password_hash:
            return False

        try:
            return bool(
                bcrypt.checkpw(
                    password.encode("utf-8"),
                    password_hash.encode("utf-8"),
                )
            )
        except (ValueError, TypeError):
            return False

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

        if len(password.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")

        if password.lower() in _COMMON_PASSWORDS:
            raise ValueError("Password is too common.")
