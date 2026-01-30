"""Helper functions for API routes."""

import logging
from typing import Any

from fastapi import HTTPException

from src.app.nexhealth.client import NexHealthClient
from src.app.nexhealth.exceptions import (
    NexHealthAPIError,
    NexHealthAuthenticationError,
    NexHealthRateLimitError,
)

logger = logging.getLogger(__name__)


async def handle_nexhealth_request(
    client: NexHealthClient,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Handle NexHealth API request with consistent error handling (DRY).

    Args:
        client: NexHealth client instance
        method: HTTP method
        path: API path
        params: Query parameters
        json: JSON body

    Returns:
        API response payload

    Raises:
        HTTPException: With appropriate status code and message
    """
    try:
        if method == "GET":
            return await client.get(path, params=params)
        elif method == "POST":
            return await client.post(path, params=params, json=json)
        elif method == "PATCH":
            return await client.patch(path, params=params, json=json)
        elif method == "DELETE":
            return await client.delete(path, params=params)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
    except NexHealthAuthenticationError as e:
        logger.error(f"Authentication error: {e}", exc_info=True)
        raise HTTPException(status_code=401, detail="NexHealth authentication failed") from e
    except NexHealthRateLimitError as e:
        logger.warning(f"Rate limit error: {e}")
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {e.retry_after}s" if e.retry_after else "Rate limit exceeded",
        ) from e
    except NexHealthAPIError as e:
        logger.error(f"API error: {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail=f"NexHealth API error: {', '.join(e.errors) if e.errors else str(e)}"
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
