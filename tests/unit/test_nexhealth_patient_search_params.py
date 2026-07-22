"""``search_patients`` must send EVERY supplied criterion to NexHealth, not
just the first one.

Regression guard for a real bug: the adapter used to pick a single search
field with an if/elif chain (email > phone > dob > name). NexHealth
AND-combines the fields server-side, so dropping all but one turned a lookup
by name + DOB into a DOB-only search — an existing patient came back as
"multiple matches" and got demoted to a can't-confirm path. Verified against
live NexHealth staging: name+DOB → 1 exact match, DOB alone → several.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.app.pms.nexhealth.adapter import (
    NexHealthAdapter,
    _normalize_phone_for_nexhealth,
)


class _Inst:
    id = "inst"
    name = "inst"


def _adapter_capturing_params(captured: dict[str, Any]) -> NexHealthAdapter:
    """Adapter whose HTTP call is stubbed to record the outgoing params."""
    adapter = NexHealthAdapter(
        client=object(),  # never used — request layer is patched out
        institution=_Inst(),
        subdomain="sub",
        location_id="loc",
    )
    return adapter


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_request(client, method, path, params=None, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params or {}
        return {"data": {"patients": []}}

    monkeypatch.setattr(
        "src.app.pms.nexhealth.adapter.handle_nexhealth_request", fake_request
    )
    return captured


@pytest.mark.asyncio
async def test_sends_name_and_dob_together(capture: dict[str, Any]) -> None:
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients(
        "ABDULLAH AHMED", date_of_birth="2000-02-02"
    )
    params = capture["params"]
    assert params["name"] == "ABDULLAH AHMED"
    assert params["date_of_birth"] == "2000-02-02"


@pytest.mark.asyncio
async def test_sends_name_and_phone_together_normalized(capture: dict[str, Any]) -> None:
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients("ALEX ALY", phone_number="(431) 450-1226")
    params = capture["params"]
    assert params["name"] == "ALEX ALY"
    assert params["phone_number"] == _normalize_phone_for_nexhealth("(431) 450-1226")


@pytest.mark.asyncio
async def test_sends_all_four_criteria(capture: dict[str, Any]) -> None:
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients(
        "Jane Doe",
        email="jane@example.com",
        phone_number="5054821234",
        date_of_birth="1990-01-01",
    )
    params = capture["params"]
    assert params["name"] == "Jane Doe"
    assert params["email"] == "jane@example.com"
    assert params["phone_number"] == "5054821234"
    assert params["date_of_birth"] == "1990-01-01"


@pytest.mark.asyncio
async def test_email_as_query_is_not_sent_as_name(capture: dict[str, Any]) -> None:
    """Callers pass name-or-email-or-phone as the positional query. When the
    query merely echoes the email, it must not also be sent as ``name`` (we
    must never issue name="foo@bar.com")."""
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients(
        "jane@example.com", email="jane@example.com"
    )
    params = capture["params"]
    assert params["email"] == "jane@example.com"
    assert "name" not in params


@pytest.mark.asyncio
async def test_phone_as_query_is_not_sent_as_name(capture: dict[str, Any]) -> None:
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients("5054821234", phone_number="5054821234")
    params = capture["params"]
    assert params["phone_number"] == "5054821234"
    assert "name" not in params


@pytest.mark.asyncio
async def test_bare_name_query_falls_back_to_name(capture: dict[str, Any]) -> None:
    adapter = _adapter_capturing_params(capture)
    await adapter.search_patients("John Smith")
    params = capture["params"]
    assert params["name"] == "John Smith"
    assert "email" not in params
    assert "phone_number" not in params
    assert "date_of_birth" not in params
