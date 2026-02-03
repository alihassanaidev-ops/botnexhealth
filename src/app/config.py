"""Application configuration."""

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret_file(file_path: str | None) -> str | None:
    """Read secret from Docker secret file if it exists."""
    if not file_path:
        return None
    path = Path(file_path)
    if path.exists():
        return path.read_text().strip()
    return None


class Settings(BaseSettings):
    """Application settings with validation."""

    # App settings
    app_env: str = "local"
    log_level: str = "info"

    # NexHealth API settings
    nexhealth_api_key: str = ""
    nexhealth_base_url: str = "https://nexhealth.info"
    nexhealth_api_version: str = "v2"
    nexhealth_accept: str = "application/vnd.Nexhealth+json;version=2"

    # Optional NexHealth settings
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

    # Retell AI settings
    retell_api_secret: str | None = None

    # Security
    admin_api_key: str = "secret-admin-key"  # Default for dev, override in env

    # GoHighLevel API settings
    ghl_api_key: str | None = None
    ghl_location_id: str = "nUR2OnxPQh3aLQXrymf6"  # Default location

    # Docker secret file paths (set via *_FILE env vars)
    nexhealth_api_key_file: str | None = None
    retell_api_secret_file: str | None = None
    admin_api_key_file: str | None = None
    ghl_api_key_file: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def load_secrets_from_files(self) -> "Settings":
        """Load secrets from Docker secret files if available."""
        # NexHealth API Key
        if secret := read_secret_file(self.nexhealth_api_key_file):
            object.__setattr__(self, "nexhealth_api_key", secret)

        # Retell API Secret
        if secret := read_secret_file(self.retell_api_secret_file):
            object.__setattr__(self, "retell_api_secret", secret)

        # Admin API Key
        if secret := read_secret_file(self.admin_api_key_file):
            object.__setattr__(self, "admin_api_key", secret)

        # GHL API Key
        if secret := read_secret_file(self.ghl_api_key_file):
            object.__setattr__(self, "ghl_api_key", secret)

        # Validate required keys
        if not self.nexhealth_api_key:
            raise ValueError(
                "NEXHEALTH_API_KEY is required. Set via environment variable or "
                "NEXHEALTH_API_KEY_FILE for Docker secrets."
            )

        return self

    @property
    def api_key(self) -> str:
        """Alias for nexhealth_api_key (implements AuthConfig protocol)."""
        return self.nexhealth_api_key

    @property
    def base_url(self) -> str:
        """Alias for nexhealth_base_url (implements AuthConfig protocol)."""
        return self.nexhealth_base_url

    @property
    def accept_header(self) -> str:
        """Alias for nexhealth_accept (implements AuthConfig protocol)."""
        return self.nexhealth_accept

    @property
    def api_version(self) -> str:
        """Alias for nexhealth_api_version (implements AuthConfig protocol)."""
        return self.nexhealth_api_version


def setup_logging(log_level: str = "info") -> None:
    """Configure application logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


settings = Settings()
setup_logging(settings.log_level)