"""Helper functions for API routes."""

import logging
import uuid
from typing import Any, Callable, Awaitable

import httpx
from fastapi import HTTPException

from src.app.nexhealth.client import NexHealthClient
from src.app.nexhealth.exceptions import (
    NexHealthAPIError,
    NexHealthAuthenticationError,
    NexHealthRateLimitError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Pagination Helpers (for internal use by Retell handlers)
# =============================================================================


async def fetch_all_pages(
    fetch_fn: Callable[[int, int], Awaitable[dict[str, Any]]],
    per_page: int = 50,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    """
    Fetch all pages from a paginated NexHealth endpoint and combine results.

    Used internally by Retell handlers that need complete data (e.g., all providers).

    Args:
        fetch_fn: Async function that takes (page, per_page) and returns NexHealth response
        per_page: Items per page (default 50 for efficiency)
        max_items: Safety limit to prevent runaway fetches (default 500)

    Returns:
        Combined list of all items from all pages

    Example:
        async def fetch_providers(page: int, per_page: int):
            return await handle_nexhealth_request(
                client, "GET", "/providers",
                params={"subdomain": subdomain, "location_id": lid, "page": page, "per_page": per_page}
            )

        all_providers = await fetch_all_pages(fetch_providers, per_page=50)
    """
    all_items: list[dict[str, Any]] = []
    page = 1

    while True:
        response = await fetch_fn(page, per_page)

        # Extract data and count from NexHealth response
        data = response.get("data", [])
        total_count = response.get("count", 0)

        # Handle nested data structures (e.g., {"data": {"patients": [...]}})
        if isinstance(data, dict):
            # Try common nested keys
            for key in ["patients", "providers", "items"]:
                if key in data:
                    data = data[key]
                    break

        if not isinstance(data, list):
            logger.warning(f"fetch_all_pages: unexpected data type {type(data)}, stopping")
            break

        all_items.extend(data)

        # Check if we've fetched all items
        if len(all_items) >= total_count:
            break

        # Safety limit
        if len(all_items) >= max_items:
            logger.warning(f"fetch_all_pages: hit max_items limit ({max_items}), stopping")
            break

        # Check if this was the last page
        if len(data) < per_page:
            break

        page += 1

        # Extra safety: don't fetch more than 10 pages
        if page > 10:
            logger.warning("fetch_all_pages: hit 10 page limit, stopping")
            break

    return all_items


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
    except NexHealthAuthenticationError:
        # No exc_info — the message can include credential-shaped strings.
        logger.error("PMS authentication error path=%s", path)
        raise HTTPException(status_code=401, detail="PMS authentication failed")
    except NexHealthRateLimitError as e:
        logger.warning("PMS rate-limit hit path=%s retry_after=%s", path, e.retry_after)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please retry shortly.",
        )
    except NexHealthAPIError as e:
        # Upstream validation errors can echo patient fields. Don't forward to
        # the client; emit a correlation id so we can trace via internal logs.
        correlation = uuid.uuid4().hex[:12]
        logger.error(
            "PMS API error correlation=%s path=%s error_count=%s",
            correlation, path, len(e.errors or []),
        )
        raise HTTPException(
            status_code=502,
            detail=f"PMS rejected the request (ref {correlation}).",
        )
    except httpx.HTTPStatusError as e:
        correlation = uuid.uuid4().hex[:12]
        body_len = len(e.response.content) if e.response.content else 0
        logger.warning(
            "Upstream HTTP error correlation=%s status=%s body_bytes=%s path=%s",
            correlation, e.response.status_code, body_len, path,
        )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Upstream returned {e.response.status_code} (ref {correlation}).",
        )
    except Exception as e:
        correlation = uuid.uuid4().hex[:12]
        logger.error(
            "Unexpected upstream error correlation=%s path=%s type=%s",
            correlation, path, type(e).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error (ref {correlation}).",
        )
