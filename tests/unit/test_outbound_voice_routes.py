"""Unit tests for Outbound Voice API routes (Plan 03 / V-8)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.app.api.routes.outbound_voice import (
    OutboundVoiceProfileCreate,
    OutboundVoiceProfileResponse,
    OutboundVoiceProfileUpdate,
    WorkflowVoiceAttemptResponse,
    _institution_id,
    create_profile,
    delete_profile,
    get_profile,
    list_attempts,
    list_profiles,
    update_profile,
)

_NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
_MOD = "src.app.api.routes.outbound_voice"


def _make_user(institution_id="inst-1", location_id=None, user_id="user-1"):
    u = MagicMock()
    u.institution_id = institution_id
    u.location_id = location_id
    u.id = user_id
    return u


def _make_profile(institution_id="inst-1", location_id="loc-1"):
    p = MagicMock()
    p.id = "prof-1"
    p.institution_id = institution_id
    p.location_id = location_id
    p.retell_agent_id = "agent_x"
    p.retell_from_number = "+15005550000"
    p.retell_llm_id = None
    p.display_name = "Front Desk"
    p.is_active = True
    p.config = None
    p.created_at = _NOW
    p.updated_at = _NOW
    return p


def _make_session(*, get_result=None, execute_rows=None, flush_exc=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.get = AsyncMock(return_value=get_result)
    session.flush = AsyncMock(side_effect=flush_exc)

    async def _refresh(obj, *a, **k):
        # Simulate the DB assigning server-default timestamps on flush/refresh.
        if getattr(obj, "created_at", None) is None:
            obj.created_at = _NOW
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = _NOW

    session.refresh = AsyncMock(side_effect=_refresh)

    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=execute_rows or [])))
    session.execute = AsyncMock(return_value=exec_result)
    return session


def _patch_db(session):
    return patch(f"{_MOD}.get_db_session", return_value=session)


# --- response mappers -------------------------------------------------------


def test_profile_response_from_model():
    resp = OutboundVoiceProfileResponse.from_model(_make_profile())
    assert resp.id == "prof-1" and resp.location_id == "loc-1"
    assert resp.retell_from_number == "+15005550000" and resp.is_active is True


def test_attempt_response_from_model():
    a = MagicMock()
    a.id = "att-1"; a.workflow_run_id = "run-1"; a.step_execution_id = "se-1"
    a.step_id = "v1"; a.attempt_number = 1; a.retell_call_id = "call_1"
    a.from_number_masked = "+*******0000"; a.to_number_masked = "+*******1234"
    a.status = "completed"; a.dial_outcome = "answered"; a.disconnection_reason = "user_hangup"
    a.error_message = None; a.created_at = _NOW
    resp = WorkflowVoiceAttemptResponse.from_model(a)
    assert resp.status == "completed" and resp.dial_outcome == "answered"
    assert resp.to_number_masked.endswith("1234")


# --- create -----------------------------------------------------------------


def test_create_profile_returns_201_and_scopes_institution_from_auth():
    session = _make_session()
    data = OutboundVoiceProfileCreate(location_id="loc-1", retell_agent_id="agent_x")
    with _patch_db(session):
        resp = asyncio.run(create_profile(data, _make_user(institution_id="inst-1")))
    session.add.assert_called_once()
    added = session.add.call_args.args[0]
    assert added.institution_id == "inst-1"      # from auth, not body
    assert added.created_by_user_id == "user-1"
    assert resp.location_id == "loc-1"


def test_create_profile_conflict_returns_409():
    session = _make_session(flush_exc=IntegrityError("stmt", {}, Exception("dup")))
    data = OutboundVoiceProfileCreate(location_id="loc-1")
    with _patch_db(session):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(create_profile(data, _make_user()))
    assert exc.value.status_code == 409


# --- get / update / delete 404 + behavior -----------------------------------


def test_get_profile_404_when_missing():
    session = _make_session(get_result=None)
    with _patch_db(session):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_profile("prof-x", _make_user()))
    assert exc.value.status_code == 404


def test_get_profile_404_on_wrong_institution():
    session = _make_session(get_result=_make_profile(institution_id="other-inst"))
    with _patch_db(session):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_profile("prof-1", _make_user(institution_id="inst-1")))
    assert exc.value.status_code == 404


def test_update_profile_applies_only_set_fields():
    profile = _make_profile()
    session = _make_session(get_result=profile)
    data = OutboundVoiceProfileUpdate(is_active=False)
    with _patch_db(session):
        resp = asyncio.run(update_profile("prof-1", data, _make_user()))
    assert profile.is_active is False       # patched
    assert profile.retell_agent_id == "agent_x"  # untouched (exclude_unset)
    assert resp.id == "prof-1"


def test_update_profile_conflict_returns_409():
    session = _make_session(get_result=_make_profile(), flush_exc=IntegrityError("s", {}, Exception("dup")))
    with _patch_db(session):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_profile("prof-1", OutboundVoiceProfileUpdate(is_active=True), _make_user()))
    assert exc.value.status_code == 409


def test_delete_profile_deletes_owned():
    profile = _make_profile()
    session = _make_session(get_result=profile)
    with _patch_db(session):
        asyncio.run(delete_profile("prof-1", _make_user()))
    session.delete.assert_awaited_once_with(profile)


# --- lists ------------------------------------------------------------------


def test_list_profiles_returns_mapped():
    session = _make_session(execute_rows=[_make_profile(), _make_profile()])
    with _patch_db(session):
        rows = asyncio.run(list_profiles(_make_user(), location_id="loc-1", is_active=True))
    assert len(rows) == 2 and all(r.location_id == "loc-1" for r in rows)


def test_list_attempts_delegates_to_helper():
    session = _make_session()
    a = MagicMock()
    a.id = "att-1"; a.workflow_run_id = "run-1"; a.step_execution_id = None
    a.step_id = "v1"; a.attempt_number = 1; a.retell_call_id = "c1"
    a.from_number_masked = None; a.to_number_masked = "+*******1234"
    a.status = "placed"; a.dial_outcome = None; a.disconnection_reason = None
    a.error_message = None; a.created_at = _NOW
    with _patch_db(session), patch(f"{_MOD}.list_voice_attempts", AsyncMock(return_value=[a])) as helper:
        rows = asyncio.run(list_attempts(_make_user(institution_id="inst-1"), workflow_run_id="run-1"))
    assert len(rows) == 1 and rows[0].status == "placed"
    # institution scoping + filter forwarded to the helper.
    assert helper.call_args.args[1] == "inst-1"
    assert helper.call_args.kwargs["workflow_run_id"] == "run-1"
