from __future__ import annotations

from typing import Any

import pytest

from src.app.nexhealth.pagination import (
    extract_list_items,
    extract_page_info,
    fetch_all_pages,
)


def test_extract_list_items_reads_legacy_nested_collection() -> None:
    payload = {
        "data": {"patients": [{"id": "pat-1"}, {"id": "pat-2"}]},
        "count": 2,
    }

    assert extract_list_items(payload, collection_key="patients") == [
        {"id": "pat-1"},
        {"id": "pat-2"},
    ]


def test_extract_list_items_reads_stable_v3_flat_collection() -> None:
    payload = {
        "data": [{"id": "pat-1"}, {"id": "pat-2"}],
        "page_info": {"has_next_page": False, "end_cursor": "cursor-2"},
    }

    assert extract_list_items(payload, collection_key="patients") == [
        {"id": "pat-1"},
        {"id": "pat-2"},
    ]
    assert extract_page_info(payload)["end_cursor"] == "cursor-2"


@pytest.mark.asyncio
async def test_fetch_all_pages_uses_legacy_offset_params_and_nested_data() -> None:
    calls: list[dict[str, Any]] = []

    async def fetch(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(params))
        if params["page"] == 1:
            return {"data": {"patients": [{"id": "pat-1"}]}, "count": 2}
        return {"data": {"patients": [{"id": "pat-2"}]}, "count": 2}

    patients = await fetch_all_pages(
        fetch,
        api_contract="legacy_v2",
        collection_key="patients",
        per_page=1,
        max_items=10,
    )

    assert patients == [{"id": "pat-1"}, {"id": "pat-2"}]
    assert calls == [{"page": 1, "per_page": 1}, {"page": 2, "per_page": 1}]


@pytest.mark.asyncio
async def test_fetch_all_pages_uses_stable_v3_cursor_params_and_page_info() -> None:
    calls: list[dict[str, Any]] = []

    async def fetch(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(params))
        if "end_cursor" not in params:
            return {
                "data": [{"id": "pat-1"}],
                "page_info": {"has_next_page": True, "end_cursor": "cursor-1"},
            }
        return {
            "data": [{"id": "pat-2"}],
            "page_info": {"has_next_page": False, "end_cursor": "cursor-2"},
        }

    patients = await fetch_all_pages(
        fetch,
        api_contract="stable_v3",
        collection_key="patients",
        per_page=1,
        max_items=10,
    )

    assert patients == [{"id": "pat-1"}, {"id": "pat-2"}]
    # Cursor mode raises per_page to the v3 ceiling (1000, capped at max_items)
    # rather than passing the caller's v2-safe value through. Every caller of
    # this helper wants the whole collection, and fewer round trips is better on
    # both latency and the per-minute rate limit — measured 28 requests/11.5s at
    # per_page=100 versus 3 requests/2.3s at 1000 for the same 2,712 rows.
    assert calls == [
        {"per_page": 10},
        {"per_page": 10, "end_cursor": "cursor-1"},
    ]


@pytest.mark.asyncio
async def test_stable_v3_uses_the_documented_max_page_size() -> None:
    """v3 documents per_page max 1000; v2 tops out at 100.

    Callers pass a v2-safe value, so cursor mode must raise it — otherwise a
    2,700-row work-window read costs 28 requests instead of 3, against a
    per-minute rate limit.
    """
    calls: list[dict[str, Any]] = []

    async def fetch(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(params))
        return {"data": [], "page_info": {"has_next_page": False}}

    await fetch_all_pages(
        fetch, api_contract="stable_v3", collection_key="working_hours",
        per_page=100, max_items=50_000,
    )

    assert calls[0]["per_page"] == 1000


@pytest.mark.asyncio
async def test_legacy_v2_keeps_the_callers_page_size() -> None:
    """v2 rejects per_page above 100, so the caller's value must survive."""
    calls: list[dict[str, Any]] = []

    async def fetch(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(params))
        return {"data": []}

    await fetch_all_pages(
        fetch, api_contract="legacy_v2", collection_key="working_hours",
        per_page=100, max_items=50_000,
    )

    assert calls[0]["per_page"] == 100
