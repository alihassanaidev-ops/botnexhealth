"""Application configuration."""

import logging
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation."""

    # App settings
    app_env: str = "local"
    log_level: str = "info"

    # NexHealth API settings
    nexhealth_api_key: str
    nexhealth_base_url: str = "https://nexhealth.info"
    nexhealth_api_version: str = "v2"
    nexhealth_accept: str = "application/vnd.Nexhealth+json;version=2"

    # Optional NexHealth settings
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

    # Retell AI settings
    retell_api_secret: str | None = "key_00519e45b34c3f29bcb93902c5c4"

    # Security
    admin_api_key: str = "secret-admin-key"  # Default for dev, override in env

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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