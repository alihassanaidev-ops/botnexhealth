"""
Security headers middleware for HIPAA compliance.

Adds recommended security headers to all responses:
- Strict-Transport-Security (HSTS): Forces HTTPS
- X-Content-Type-Options: Prevents MIME sniffing
- X-Frame-Options: Prevents clickjacking
- X-XSS-Protection: Legacy XSS protection hint
- Referrer-Policy: Limits referrer leakage
- Cache-Control: Prevents caching of sensitive responses
- Permissions-Policy: Restricts browser feature access
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Force HTTPS (1 year, include subdomains)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Legacy XSS filter (for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Limit referrer information leakage
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Prevent caching of API responses (PHI safety)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"

        # Restrict browser features
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )

        return response
