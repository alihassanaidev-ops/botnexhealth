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

        # Limit referrer information leakage. setdefault, not assignment: a
        # route serving a tokenised patient link sets no-referrer deliberately,
        # and strict-origin-when-cross-origin still sends the full URL — token
        # and all — to anything same-origin on the page. Never downgrade a
        # stricter policy a route has already chosen.
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        # Prevent caching of API responses (PHI safety)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"

        # Restrict browser features
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )

        # Content-Security-Policy. The API answers JSON, which never executes
        # scripts, so a locked-down policy costs nothing. The interactive
        # /docs, /redoc and /openapi.json pages load their assets from a CDN
        # and keep their own defaults, so they are skipped. setdefault lets a
        # route opt into something stricter.
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'",
            )

        return response
