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
    assert calls == [
        {"per_page": 1},
        {"per_page": 1, "end_cursor": "cursor-1"},
    ]
