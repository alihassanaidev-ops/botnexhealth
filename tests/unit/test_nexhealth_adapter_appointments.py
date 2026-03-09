from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.pms.nexhealth import adapter as adapter_module
from src.app.pms.nexhealth.adapter import NexHealthAdapter


def _make_adapter() -> NexHealthAdapter:
    return NexHealthAdapter(
        client=SimpleNamespace(),
        institution=SimpleNamespace(),
        subdomain="test-subdomain",
        location_id="test-location",
    )


@pytest.mark.asyncio
async def test_has_provider_appointments_scans_multiple_pages(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if params["page"] == 1:
            return {
                "data": [{"id": i, "cancelled": True} for i in range(50)],
            }
        if params["page"] == 2:
            return {"data": [{"id": 999, "cancelled": False}]}
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")

    assert result is True
    assert len(calls) == 2
    assert calls[0]["provider_id"] == "123"


@pytest.mark.asyncio
async def test_has_provider_appointments_returns_false_when_all_cancelled(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()

    async def fake_request(client, method, path, params=None, json=None):
        return {"data": [{"id": 1, "cancelled": True}]}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")
    assert result is False


@pytest.mark.asyncio
async def test_has_provider_appointments_safe_fallback_on_unexpected_payload(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()

    async def fake_request(client, method, path, params=None, json=None):
        return {"data": {"appointments": []}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")
    assert result is True
