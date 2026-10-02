"""Gunicorn runtime settings.

Keep worker count controlled by deployment config instead of baking it
into the image. More processes are not free: each process owns its own
SQLAlchemy pool, so increasing WEB_CONCURRENCY directly increases the
maximum number of PostgreSQL connections.
"""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


worker_class = "uvicorn.workers.UvicornWorker"
workers = _int_env("WEB_CONCURRENCY", 2)
bind = "0.0.0.0:8000"
# Uvicorn reconstructs the public request scheme from X-Forwarded-Proto only
# when the immediate peer is trusted. Twilio signs the externally visible HTTPS
# webhook URL, so leaving Gunicorn's loopback-only default behind an ALB makes
# every otherwise-valid webhook signature fail against an internal http URL.
forwarded_allow_ips = os.getenv("TRUSTED_PROXY_CIDRS", "127.0.0.1,::1")
timeout = 120
graceful_timeout = 30
keepalive = 5
max_requests = 10000
max_requests_jitter = 1000
accesslog = "-"
# Gunicorn's default format includes the complete request line, including the
# query string. Patient search filters are PHI, so keep access logs useful for
# tracing without persisting query parameters.
access_log_format = '%({X-Request-ID}i)s "%m %U %H" %s %b'
errorlog = "-"
capture_output = True
enable_stdio_inheritance = True
