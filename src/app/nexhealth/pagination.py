"""NexHealth list response extraction and pagination helpers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from math import ceil
from typing import Any

from src.app.nexhealth.api_contract import (
    NexHealthAPIContract,
    normalize_nexhealth_api_contract,
)

logger = logging.getLogger(__name__)

PageFetcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_COMMON_COLLECTION_KEYS = (
    "patients",
    "providers",
    "operatories",
    "availabilities",
    "working_hours",
    "patient_recalls",
    "recalls",
    "items",
)


def extract_list_items(
    response: dict[str, Any],
    *,
    collection_key: str | None = None,
) -> list[dict[str, Any]]:
    """Extract list rows from v2 nested and v3 flat NexHealth envelopes.

    v2 list endpoints commonly return ``{"data": {"patients": [...]}}`` while
    v3 cursor-paginated endpoints return ``{"data": [...]}``. A few endpoints
    have historically drifted between flat and named lists, so callers pass the
    expected collection key and this helper keeps the compatibility rule in one
    tested place.
    """
    data = response.get("data") if isinstance(response, dict) else None

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        keys = _COMMON_COLLECTION_KEYS
        if collection_key:
            keys = (collection_key, *(
                key for key in _COMMON_COLLECTION_KEYS if key != collection_key
            ))
        for key in keys:
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]

    return []


def extract_page_info(response: dict[str, Any]) -> dict[str, Any]:
    page_info = response.get("page_info") if isinstance(response, dict) else None
    return page_info if isinstance(page_info, dict) else {}


async def fetch_all_pages(
    fetch_page: PageFetcher,
    *,
    api_contract: NexHealthAPIContract | str,
    collection_key: str | None = None,
    per_page: int = 50,
    max_items: int = 500,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a bounded NexHealth list under the selected API contract.

    ``fetch_page`` receives only pagination params. The caller owns endpoint
    filters such as subdomain, location, dates, and includes.
    """
    if max_items <= 0:
        return []
    if per_page <= 0:
        raise ValueError("per_page must be greater than zero")

    contract = normalize_nexhealth_api_contract(api_contract)
    page_limit = max_pages or max(1, ceil(max_items / per_page))

    if contract is NexHealthAPIContract.STABLE_V3:
        return await _fetch_cursor_pages(
            fetch_page,
            collection_key=collection_key,
            per_page=per_page,
            max_items=max_items,
            max_pages=page_limit,
        )

    return await _fetch_offset_pages(
        fetch_page,
        collection_key=collection_key,
        per_page=per_page,
        max_items=max_items,
        max_pages=page_limit,
    )


async def _fetch_offset_pages(
    fetch_page: PageFetcher,
    *,
    collection_key: str | None,
    per_page: int,
    max_items: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        response = await fetch_page({"page": page, "per_page": per_page})
        items = extract_list_items(response, collection_key=collection_key)
        all_items.extend(items)

        if len(all_items) >= max_items:
            logger.warning(
                "fetch_all_pages: hit max_items limit (%s), stopping", max_items
            )
            return all_items[:max_items]

        total_count = response.get("count") if isinstance(response, dict) else None
        if isinstance(total_count, int):
            if (total_count == 0 and not items) or (
                total_count > 0 and len(all_items) >= total_count
            ):
                break

        if len(items) < per_page:
            break
    else:
        logger.warning("fetch_all_pages: hit %s page limit, stopping", max_pages)

    return all_items


async def _fetch_cursor_pages(
    fetch_page: PageFetcher,
    *,
    collection_key: str | None,
    per_page: int,
    max_items: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    end_cursor: str | None = None

    for _page in range(1, max_pages + 1):
        params: dict[str, Any] = {"per_page": per_page}
        if end_cursor:
            params["end_cursor"] = end_cursor

        response = await fetch_page(params)
        items = extract_list_items(response, collection_key=collection_key)
        all_items.extend(items)

        if len(all_items) >= max_items:
            logger.warning(
                "fetch_all_pages: hit max_items limit (%s), stopping", max_items
            )
            return all_items[:max_items]

        page_info = extract_page_info(response)
        if not page_info:
            if len(items) >= per_page:
                logger.warning(
                    "fetch_all_pages: v3 response missing page_info, stopping"
                )
            break

        if not page_info.get("has_next_page"):
            break

        next_cursor = page_info.get("end_cursor")
        if not next_cursor:
            logger.warning(
                "fetch_all_pages: v3 page_info has next page but no end_cursor, stopping"
            )
            break
        end_cursor = str(next_cursor)
    else:
        logger.warning("fetch_all_pages: hit %s cursor page limit, stopping", max_pages)

    return all_items
