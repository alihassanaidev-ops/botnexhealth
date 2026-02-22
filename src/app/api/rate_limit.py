"""Shared rate limiter for API endpoints.

Uses slowapi with client IP as the key. Import `limiter` in route
modules and decorate endpoints with `@limiter.limit(...)`.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# ── Standard rate tiers ──────────────────────────────────────────────
# Use these constants for consistency across route modules.

RATE_READ = "60/minute"     # GET endpoints (list / detail)
RATE_WRITE = "20/minute"    # POST / PATCH / DELETE endpoints
RATE_AUTH = "5/minute"      # Login / token exchange
