from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.pms import factory


@pytest.mark.asyncio
async def test_factory_routes_gotracker_to_gotracker_adapter(monkeypatch: pytest.MonkeyPatch):
    created = SimpleNamespace(source="gotracker")

    async def fake_create(institution, location):
        assert institution.pms_type == "gotracker"
        assert location.slug == "downtown"
        return created

    from src.app.pms.gotracker.adapter import GoTrackerAdapter

    monkeypatch.setattr(GoTrackerAdapter, "create", fake_create)

    adapter = await factory.get_adapter_for_institution_location(
        SimpleNamespace(pms_type="gotracker", slug="clinic"),
        SimpleNamespace(slug="downtown"),
    )

    assert adapter is created


@pytest.mark.asyncio
async def test_factory_still_blocks_no_pms() -> None:
    with pytest.raises(HTTPException) as exc:
        await factory.get_adapter_for_institution_location(
            SimpleNamespace(pms_type="none", slug="clinic"),
            SimpleNamespace(slug="downtown"),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_factory_rejects_unknown_pms() -> None:
    with pytest.raises(HTTPException) as exc:
        await factory.get_adapter_for_institution_location(
            SimpleNamespace(pms_type="other", slug="clinic"),
            SimpleNamespace(slug="downtown"),
        )

    assert exc.value.status_code == 409
    assert "Unsupported PMS integration" in exc.value.detail
