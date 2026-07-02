"""Application configuration."""

import ipaddress
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import structlog
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


def normalize_redis_url(url: str | None) -> str | None:
    """Ensure TLS Redis URLs include an explicit certificate policy."""
    if not url:
        return None

    parsed = urlparse(url)
    if parsed.scheme != "rediss":
        return url

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("ssl_cert_reqs", os.getenv("REDIS_SSL_CERT_REQS", "required"))

    return urlunparse(parsed._replace(query=urlencode(query)))


def build_database_url(
    *,
    username: str | None,
    password: str | None,
    host: str | None,
    port: int | None,
    database_name: str | None,
) -> str | None:
    """Compose an asyncpg connection URL from discrete database settings."""
    if not all([username, password, host, port, database_name]):
        return None

    return (
        "postgresql+asyncpg://"
        f"{quote_plus(username)}:{quote_plus(password)}@"
        f"{host}:{port}/{database_name}"
    )


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
    nexhealth_max_connections: int = 20
    nexhealth_max_keepalive_connections: int = 10

    # Optional NexHealth settings
    nexhealth_subdomain: str | None = None
    nexhealth_location_id: str | None = None

    # Retell AI settings
    retell_api_secret: str | None = None

    # Resend (transactional email)
    resend_api_key: str | None = None
    resend_from_email: str | None = None
    resend_reply_to: str | None = None
    # Comma-separated fallback recipients for call alerts (optional)
    resend_alert_recipients: str | None = None

    # Celery
    celery_broker_url: str | None = None
    redis_url: str | None = None

    # AWS S3 (call recordings)
    aws_s3_bucket_name: str | None = None
    aws_region: str = "ca-central-1"

    # PHI retention windows (days unless otherwise noted)
    retention_clinical_record_days: int = 3650
    retention_minor_record_age_years: int = 28
    retention_recording_days: int = 90
    retention_sms_body_days: int = 3650
    retention_sms_metadata_days: int = 2190
    retention_notification_days: int = 180
    retention_dead_letter_raw_days: int = 30
    retention_idempotency_days: int = 7


    # Twilio (SMS / phone numbers)
    twillio_sid: str | None = None          # Account SID (env: TWILLIO_SID)
    twillio_api_secret: str | None = None   # Auth Token (env: TWILLIO_API_SECRET)
    twilio_sms_status_callback_url: str | None = None

    # Database (PostgreSQL)
    database_url: str | None = None
    database_host: str | None = None
    database_port: int | None = None
    database_name: str | None = None
    database_user: str | None = None
    database_password: str | None = None
    database_pool_size: int = 5
    database_max_overflow: int = 5
    database_pool_timeout_seconds: int = 10
    database_pool_recycle_seconds: int = 1800
    encryption_key: str | None = None

    # Account lockout (HIPAA §164.312(d))
    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 30
    access_token_ttl_minutes: int = 15
    # HIPAA §164.312(a)(2)(iii) "automatic logoff": short-circuit a stale
    # session if the dashboard is left open and idle. Each /refresh resets
    # this window, so an actively-used SPA stays logged in indefinitely;
    # a walked-away tab expires after the configured idle window.
    refresh_token_ttl_minutes: int = 60
    invite_token_ttl_hours: int = 72
    password_reset_token_ttl_minutes: int = 60
    auth_frontend_base_url: str | None = None
    auth_redirect_allowed_hosts: str = ""
    webauthn_rp_id: str | None = None
    webauthn_rp_name: str = "ScaleNexus Dashboard"
    webauthn_allowed_origins: str = ""
    # WebAuthn user-verification strictness. Default REQUIRED demands
    # the authenticator prove inherence (biometric or PIN) at every
    # touch — a passkey behaves as a full factor. Set to False on
    # deployments that must accommodate older non-UV-capable security
    # keys; in that mode the platform falls back to UserVerification=
    # PREFERRED, which accepts a bare presence tap. Keep True for any
    # environment with modern devices.
    webauthn_user_verification_strict: bool = True
    # Local-dev escape hatch for machines without a configured passkey provider.
    # Ignored in production; SUPER_ADMIN remains passkey-only there.
    dev_allow_super_admin_totp: bool = False

    # CORS — comma-separated allowed origins; defaults to "*" for local dev only
    cors_allowed_origins: str = "*"

    # Refresh-token cookie. Browsers special-case localhost so Secure=True works
    # over HTTP locally; override only if running dev on a non-localhost host.
    refresh_cookie_name: str = "refresh_token"
    refresh_cookie_path: str = "/api/auth"
    cookie_secure: bool = True
    cookie_samesite: str = "strict"

    # Proxy / request source validation
    trusted_proxy_cidrs: str = ""

    # Docker secret file paths (set via *_FILE env vars)
    nexhealth_api_key_file: str | None = None
    retell_api_secret_file: str | None = None
    # Auth / JWT (REQUIRED — no defaults, must be set in .env or secrets manager)
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "nexhealth-api"
    jwt_audience: str = "nexhealth-dashboard"
    jwt_secret_file: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def load_secrets_from_files(self) -> "Settings":
        """Load secrets from Docker secret files if available."""
        # NexHealth API Key
        if secret := read_secret_file(self.nexhealth_api_key_file):
            object.__setattr__(self, "nexhealth_api_key", secret)

        # Retell API Secret
        if secret := read_secret_file(self.retell_api_secret_file):
            object.__setattr__(self, "retell_api_secret", secret)


        # JWT Secret
        if secret := read_secret_file(self.jwt_secret_file):
            object.__setattr__(self, "jwt_secret", secret)

        # Block wildcard CORS in production
        if self.is_production and self.cors_allowed_origins.strip() == "*":
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must not be '*' in production. "
                "Set explicit origins, e.g. 'https://dashboard.yourdomain.com'"
            )

        # ENCRYPTION_KEY is the AES-256-GCM key for PHI columns (call
        # transcripts, contact phone/email, SMS bodies, etc). It MUST be set
        # explicitly in production and MUST be distinct from JWT_SECRET — see
        # docs/PRINCIPAL_REVIEW.md. The same key material is reused (via
        # HKDF) for keyed hashes (phone-hash, retell-log-hash), so rotating
        # JWT_SECRET should never invalidate PHI lookups.
        if self.is_production and not self.encryption_key:
            raise ValueError(
                "ENCRYPTION_KEY must be set in production. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        if (
            self.is_production
            and self.encryption_key
            and self.encryption_key == self.jwt_secret
        ):
            raise ValueError(
                "ENCRYPTION_KEY must be distinct from JWT_SECRET. "
                "Reusing the same secret means JWT rotation invalidates encrypted PHI."
            )

        if self.cookie_samesite.lower() not in {"strict", "lax", "none"}:
            raise ValueError(
                "COOKIE_SAMESITE must be 'strict', 'lax', or 'none'."
            )
        if self.is_production and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production.")
        if self.cookie_samesite.lower() == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true.")

        if (
            self.is_production
            and self.auth_frontend_base_url
            and urlparse(self.auth_frontend_base_url).scheme != "https"
        ):
            raise ValueError("AUTH_FRONTEND_BASE_URL must use https in production")

        if self.is_production and not self.webauthn_rp_id:
            raise ValueError("WEBAUTHN_RP_ID must be set in production")
        if self.is_production and not self.webauthn_allowed_origins.strip():
            raise ValueError("WEBAUTHN_ALLOWED_ORIGINS must be set in production")
        if self.is_production:
            for origin in self._split_csv(self.webauthn_allowed_origins):
                parsed = urlparse(origin)
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError(
                        "WEBAUTHN_ALLOWED_ORIGINS must contain HTTPS origins in production"
                    )

        for cidr in self._split_csv(self.trusted_proxy_cidrs):
            ipaddress.ip_network(cidr, strict=False)

        retention_fields = (
            "retention_clinical_record_days",
            "retention_minor_record_age_years",
            "retention_recording_days",
            "retention_sms_body_days",
            "retention_sms_metadata_days",
            "retention_notification_days",
            "retention_dead_letter_raw_days",
            "retention_idempotency_days",
        )
        for field_name in retention_fields:
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name.upper()} must be at least 1")

        if not self.database_url:
            database_url = build_database_url(
                username=self.database_user,
                password=self.database_password,
                host=self.database_host,
                port=self.database_port,
                database_name=self.database_name,
            )
            if database_url:
                object.__setattr__(self, "database_url", database_url)

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

    @property
    def normalized_redis_url(self) -> str | None:
        """Redis URL with TLS requirements normalized for managed Redis."""
        return normalize_redis_url(self.redis_url)

    @property
    def normalized_celery_broker_url(self) -> str | None:
        """Broker URL with TLS requirements normalized for managed Redis."""
        return normalize_redis_url(self.celery_broker_url)

    @property
    def effective_redis_url(self) -> str | None:
        """Return the best available Redis URL for session storage."""
        return self.normalized_redis_url or self.normalized_celery_broker_url

    @property
    def effective_webauthn_rp_id(self) -> str:
        return (self.webauthn_rp_id or "localhost").strip()

    @property
    def effective_webauthn_allowed_origins(self) -> list[str]:
        explicit = self._split_csv(self.webauthn_allowed_origins)
        if explicit:
            return [origin.rstrip("/") for origin in explicit]
        if self.auth_frontend_base_url:
            return [self.auth_frontend_base_url.rstrip("/")]
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]

    @staticmethod
    def _split_csv(raw: str | None) -> list[str]:
        return [item.strip() for item in (raw or "").split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def allow_super_admin_totp(self) -> bool:
        return not self.is_production and self.dev_allow_super_admin_totp

    @property
    def allowed_auth_redirect_netlocs(self) -> frozenset[str]:
        allowed: set[str] = set()
        if self.auth_frontend_base_url:
            netloc = urlparse(self.auth_frontend_base_url).netloc
            if netloc:
                allowed.add(netloc.lower())

        for host in self._split_csv(self.auth_redirect_allowed_hosts):
            parsed = urlparse(host if "://" in host else f"https://{host}")
            netloc = parsed.netloc or parsed.path
            if netloc:
                allowed.add(netloc.lower())

        return frozenset(allowed)

    @property
    def trusted_proxy_networks(
        self,
    ) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(cidr, strict=False)
            for cidr in self._split_csv(self.trusted_proxy_cidrs)
        )



def setup_logging(log_level: str = "info", app_env: str = "local") -> None:
    """Configure application logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    is_dev = app_env in {"local", "dev", "test"}
    renderer = (
        structlog.dev.ConsoleRenderer(colors=False)
        if is_dev
        else structlog.processors.JSONRenderer()
    )
    exception_processors = (
        [] if is_dev else [structlog.processors.format_exc_info]
    )
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        *exception_processors,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    capture_handlers = [
        existing_handler
        for existing_handler in root_logger.handlers
        if existing_handler.__class__.__name__ == "LogCaptureHandler"
        and existing_handler.__class__.__module__.startswith("_pytest.")
    ]
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    for capture_handler in capture_handlers:
        root_logger.addHandler(capture_handler)
    root_logger.setLevel(level)


settings = Settings()
setup_logging(settings.log_level, settings.app_env)


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings
