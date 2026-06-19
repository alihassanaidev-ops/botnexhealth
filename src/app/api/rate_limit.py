"""Shared rate limiter for API endpoints.

Uses slowapi with the resolved client IP as the key. The IP is taken from
``X-Forwarded-For`` only when the immediate peer is in a trusted proxy
range — otherwise the direct peer is used. This stops a caller bypassing
the ALB from forging the rate-limit key.

When Redis is configured the limiter uses it as shared storage so all
gunicorn workers count against the same bucket; otherwise it falls back
to per-process in-memory storage (acceptable for ``local``/``test``).
"""

from __future__ import annotations

import logging

from fastapi import Request
from slowapi import Limiter

from src.app.config import settings
from src.app.security import get_client_ip

logger = logging.getLogger(__name__)


def _proxy_aware_key(request: Request) -> str:
    """Use the trusted-proxy-aware client IP as the rate-limit key."""
    direct_host = request.client.host if request.client else None
    resolved = get_client_ip(
        forwarded_for=request.headers.get("x-forwarded-for"),
        direct_host=direct_host,
    )
    # Fall back to direct host (or "unknown") so the limiter never raises.
    return resolved or direct_host or "unknown"


_storage_uri = settings.effective_redis_url

# Load-test environments (app_env == "loadtest") disable the per-IP limiter so a
# load generator measures the application, not the limiter. ``is_production`` is
# mutually exclusive with "loadtest" (see ``Settings.is_production``), so a
# production deployment can never reach this branch.
_rate_limit_enabled = settings.app_env.lower() not in {"loadtest", "load_test"}
if not _rate_limit_enabled:
    logger.warning(
        "Rate limiting DISABLED (app_env=%s) — load-test environments only.",
        settings.app_env,
    )

if _storage_uri:
    limiter = Limiter(
        key_func=_proxy_aware_key,
        storage_uri=_storage_uri,
        enabled=_rate_limit_enabled,
    )
else:
    # In-memory storage is per-process. Acceptable for local/test, NOT for
    # production: with multiple gunicorn workers the published rate is
    # silently multiplied by the worker count. Refuse to start.
    if settings.is_production:
        raise RuntimeError(
            "Rate limiter requires REDIS_URL (or CELERY_BROKER_URL) in production. "
            "Per-process in-memory counters multiply the published rate by the worker "
            "count and silently break the security guarantee."
        )
    logger.warning(
        "Rate limiter using in-memory storage. Acceptable for local/test only."
    )
    limiter = Limiter(key_func=_proxy_aware_key, enabled=_rate_limit_enabled)


# ── Standard rate tiers ──────────────────────────────────────────────
# Use these constants for consistency across route modules.

RATE_READ = "60/minute"     # GET endpoints (list / detail)
RATE_WRITE = "20/minute"    # POST / PATCH / DELETE endpoints
RATE_AUTH = "10/minute"     # Login / token exchange — tight enough to slow
                            # password-spray, loose enough to tolerate typos.
