"""Sikka API exceptions."""


class SikkaError(Exception):
    """Base exception for Sikka API errors."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        http_code: int | None = None,
        short_message: str | None = None,
        long_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.http_code = http_code
        self.short_message = short_message
        self.long_message = long_message


class SikkaAuthenticationError(SikkaError):
    """Raised when authentication fails (API2003, API2004, API2007)."""

    pass


class SikkaAPIError(SikkaError):
    """Raised when API returns an error response."""

    pass


class SikkaRateLimitError(SikkaError):
    """Raised when rate limit is exceeded (API2011, API2012)."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code)
        self.retry_after = retry_after


class SikkaResourceNotFoundError(SikkaError):
    """Raised when resource is not found (API2009)."""

    pass


class SikkaConflictError(SikkaError):
    """Raised when resource already exists (API2013, API2015)."""

    pass
