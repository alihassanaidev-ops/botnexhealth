"""NexHealth API exceptions."""

from __future__ import annotations


class NexHealthError(RuntimeError):
    """Base exception for NexHealth API errors."""

    pass


class NexHealthAuthenticationError(NexHealthError):
    """Raised when authentication fails."""

    pass


class NexHealthAPIError(NexHealthError):
    """Raised when API returns an error response."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class NexHealthRateLimitError(NexHealthError):
    """Raised when rate limit is exceeded (429)."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
