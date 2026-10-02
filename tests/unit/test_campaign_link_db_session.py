from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app import database


class _Context:
    def __init__(self, session):  # noqa: ANN001
        self.session = session

    async def __aenter__(self):  # noqa: ANN204
        return self.session

    async def __aexit__(self, *_args):  # noqa: ANN204
        return False


@pytest.mark.asyncio
async def test_campaign_link_lookup_reopens_in_resolved_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = AsyncMock()
    result = AsyncMock()
    result.one_or_none = lambda: SimpleNamespace(
        institution_id="11111111-1111-1111-1111-111111111111",
        location_id="22222222-2222-2222-2222-222222222222",
    )
    lookup.execute = AsyncMock(return_value=result)
    tenant = AsyncMock()
    calls: list[tuple[str, dict[str, str | None]]] = []

    def fake_system_session(context_type: str, **kwargs):  # noqa: ANN003, ANN202
        calls.append((context_type, kwargs))
        return _Context(lookup if context_type == "campaign_link_lookup" else tenant)

    monkeypatch.setattr(database, "get_system_db_session", fake_system_session)

    async with database.get_campaign_link_db_session("run-1") as session:
        assert session is tenant

    assert calls == [
        ("campaign_link_lookup", {"external_id": "run-1"}),
        (
            "celery",
            {
                "institution_id": "11111111-1111-1111-1111-111111111111",
                "location_id": "22222222-2222-2222-2222-222222222222",
                "external_id": "campaign_link:run-1",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_unknown_campaign_link_never_opens_a_tenant_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = AsyncMock()
    result = AsyncMock()
    result.one_or_none = lambda: None
    lookup.execute = AsyncMock(return_value=result)
    calls: list[str] = []

    def fake_system_session(context_type: str, **_kwargs):  # noqa: ANN003, ANN202
        calls.append(context_type)
        return _Context(lookup)

    monkeypatch.setattr(database, "get_system_db_session", fake_system_session)

    async with database.get_campaign_link_db_session("missing") as session:
        assert session is lookup

    assert calls == ["campaign_link_lookup"]
