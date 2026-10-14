"""Recall calls only see and book the campaign's allowed appointment types."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.retell import handlers
from src.app.services.automation.recall_booking_guard import (
    appointment_type_allowed,
    resolve_recall_appointment_types_for_call,
)

ALLOWED = frozenset({"1230521", "1230529"})  # Adult recall, Teen Recall


def _unwrap(fn):
    return getattr(fn, "__wrapped__", fn)


def _definition(allowed=None, recall=True):
    trigger = (
        {
            "type": "schedule",
            "cron": "0 9 * * *",
            "source": {
                "kind": "pms_recall",
                "recall_interval_months": 6,
                "allowed_appointment_type_ids": allowed or [],
            },
        }
        if recall
        else {"type": "manual"}
    )
    return {
        "trigger": trigger,
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }


def _session(definition, attempt=("run-1",)):
    session = AsyncMock()
    result = MagicMock()
    result.first.return_value = attempt
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(workflow_version_id="ver-1"),
            SimpleNamespace(definition=definition),
        ]
    )
    return session


# --- resolving the allowed list for a call ---------------------------------


@pytest.mark.asyncio
async def test_no_call_id_is_unrestricted():
    assert await resolve_recall_appointment_types_for_call(AsyncMock(), None) is None


@pytest.mark.asyncio
async def test_inbound_call_without_a_campaign_attempt_is_unrestricted():
    session = _session(_definition(["nh-1230521"]), attempt=None)
    assert await resolve_recall_appointment_types_for_call(session, "call_1") is None


@pytest.mark.asyncio
async def test_non_recall_campaign_is_unrestricted():
    session = _session(_definition(recall=False))
    assert await resolve_recall_appointment_types_for_call(session, "call_1") is None


@pytest.mark.asyncio
async def test_recall_campaign_without_a_list_is_unrestricted():
    session = _session(_definition([]))
    assert await resolve_recall_appointment_types_for_call(session, "call_1") is None


@pytest.mark.asyncio
async def test_recall_campaign_list_is_normalized():
    session = _session(_definition(["nh-1230521", " 1230529 "]))
    assert await resolve_recall_appointment_types_for_call(session, "call_1") == ALLOWED


def test_allowed_check_accepts_prefixed_and_raw_ids():
    assert appointment_type_allowed(ALLOWED, "nh-1230521")
    assert appointment_type_allowed(ALLOWED, 1230529)
    assert not appointment_type_allowed(ALLOWED, "nh-1230532")
    assert not appointment_type_allowed(ALLOWED, None)


# --- the Retell tools ---------------------------------------------------------


def _patch(monkeypatch, ctx, allowed):
    async def _resolve():
        return ctx

    async def _allowed():
        return allowed

    monkeypatch.setattr(handlers, "_resolve_context", _resolve)
    monkeypatch.setattr(handlers, "_recall_allowed_appointment_types", _allowed)


def _types_ctx():
    adapter = MagicMock()
    adapter.list_appointment_types = AsyncMock(
        return_value=[
            SimpleNamespace(id=f"nh-{tid}", name=name, duration_minutes=30, source_metadata={})
            for tid, name in (("1230521", "Adult recall (16+)"), ("1230529", "Teen Recall (13-15)"), ("1230532", "Crown"))
        ]
    )
    return SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=None, adapter=adapter)


@pytest.mark.asyncio
async def test_recall_call_lists_only_allowed_types(monkeypatch):
    _patch(monkeypatch, _types_ctx(), ALLOWED)
    result = await _unwrap(handlers.list_appointment_types)({})
    assert [t["name"] for t in result["appointment_types"]] == [
        "Adult recall (16+)",
        "Teen Recall (13-15)",
    ]


@pytest.mark.asyncio
async def test_unrestricted_call_lists_every_type(monkeypatch):
    _patch(monkeypatch, _types_ctx(), None)
    result = await _unwrap(handlers.list_appointment_types)({})
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_recall_call_providers_show_only_allowed_types(monkeypatch):
    def provider(pid, types):
        return SimpleNamespace(
            id=pid, name=pid, first_name=pid, last_name="", specialty=None, operatory_ids=[],
            appointment_types=[{"id": f"nh-{t}", "name": t} for t in types],
        )

    adapter = MagicMock()
    adapter.list_providers = AsyncMock(
        return_value=[provider("hygienist", ["1230521", "1230529"]), provider("dentist", ["1230532"])]
    )
    ctx = SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=None, adapter=adapter)
    _patch(monkeypatch, ctx, ALLOWED)

    result = await _unwrap(handlers.list_providers)({})

    assert [p["id"] for p in result["providers"]] == ["hygienist"]
    assert {t["id"] for t in result["providers"][0]["appointment_types"]} == {
        "nh-1230521",
        "nh-1230529",
    }


@pytest.mark.asyncio
async def test_recall_call_cannot_search_a_disallowed_type(monkeypatch):
    adapter = MagicMock()
    adapter.source = "nexhealth"
    adapter.find_available_slots = AsyncMock()
    ctx = SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=None, adapter=adapter)
    _patch(monkeypatch, ctx, ALLOWED)

    result = await _unwrap(handlers.find_appointment_slots)(
        {"appointment_type_id": "nh-1230532", "start_date": "2026-10-20"}
    )

    assert result == {"error": handlers._RECALL_TYPE_REFUSAL}
    adapter.find_available_slots.assert_not_called()


@pytest.mark.asyncio
async def test_recall_call_cannot_book_a_disallowed_type(monkeypatch):
    adapter = MagicMock()
    adapter.book_appointment = AsyncMock()
    ctx = SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=None, adapter=adapter)
    _patch(monkeypatch, ctx, ALLOWED)

    result = await _unwrap(handlers.book_appointment)(
        {"appointment_type_id": "nh-1230532", "provider_id": "nh-1", "patient_id": "nh-2",
         "start_time": "2026-10-20T09:00:00-07:00"}
    )

    assert result == {"success": False, "error": handlers._RECALL_TYPE_REFUSAL}
    adapter.book_appointment.assert_not_called()
