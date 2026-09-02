"""Tenant inheritance and scope tests for managed inbound email settings."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.routes.email_inbox_settings import _scope_for_user
from src.app.models.email_inbox_setting import EmailInboxSetting
from src.app.models.user import User, UserRole
from src.app.services.email.inbox_settings_service import InboxSettingsService


INST = "11111111-2222-3333-4444-555555555555"
LOCATION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _user(role: UserRole, *, institution_id: str | None = INST, location_id=None):
    return User(
        id="99999999-8888-7777-6666-555555555555",
        email="admin@example.com",
        role=role.value,
        institution_id=institution_id,
        location_id=location_id,
        is_active=True,
    )


def test_location_admin_is_forced_to_own_location():
    user = _user(UserRole.LOCATION_ADMIN, location_id=LOCATION)
    assert _scope_for_user(user, None, None) == (INST, LOCATION)
    assert _scope_for_user(user, INST, LOCATION) == (INST, LOCATION)
    with pytest.raises(HTTPException):
        _scope_for_user(user, "00000000-0000-0000-0000-000000000000", LOCATION)


def test_location_inherits_institution_default_and_gets_signed_address():
    default = EmailInboxSetting(
        institution_id=INST,
        location_id=None,
        is_enabled=True,
        allow_new_contacts=True,
        stop_automation_on_reply=False,
    )
    default.forward_to = "frontdesk@example.com"
    session = AsyncMock()
    exact_result = MagicMock()
    exact_result.scalar_one_or_none.return_value = None
    default_result = MagicMock()
    default_result.scalar_one_or_none.return_value = default
    session.execute.side_effect = [exact_result, default_result]

    with patch("src.app.services.email.inbox_settings_service.settings") as configured:
        configured.ses_inbound_domain = "inbound.example.com"
        configured.ses_inbound_bucket = "bucket"
        configured.ses_inbound_queue_url = "queue"
        value = asyncio.run(InboxSettingsService(session).get(INST, LOCATION))
        assert value.platform_ready is True
        assert value.inbox_address.endswith("@inbound.example.com")

    assert value.inherited is True
    assert value.is_enabled is True
    assert value.allow_new_contacts is True
    assert value.stop_automation_on_reply is False
    assert value.forward_to == "frontdesk@example.com"


def test_institution_default_is_control_only_not_a_shared_address():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    with patch("src.app.services.email.inbox_settings_service.settings") as configured:
        configured.ses_inbound_domain = "inbound.example.com"
        configured.ses_inbound_bucket = "bucket"
        configured.ses_inbound_queue_url = "queue"
        value = asyncio.run(InboxSettingsService(session).get(INST))

    assert value.inbox_address is None
