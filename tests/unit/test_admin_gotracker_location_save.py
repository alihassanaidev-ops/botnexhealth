from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.admin_institutions import (
    _ensure_gotracker_webhook_after_location_save,
)


def _session_without_subscription() -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_location_save_reconciles_gotracker_webhook_immediately(monkeypatch):
    session = _session_without_subscription()
    institution = SimpleNamespace(id="inst-1", pms_type="gotracker")
    location = SimpleNamespace(
        id="loc-1",
        gotracker_product_key_encrypted="encrypted-key",
        gotracker_webhook_secret=None,
    )
    row = SimpleNamespace(provider_subscription_id="sub-1", status="active")
    svc = MagicMock()
    svc.ensure_location_subscription = AsyncMock(return_value=(row, True))

    monkeypatch.setattr(
        "src.app.api.routes.admin_institutions.settings.gotracker_webhook_callback_base_url",
        "https://api.example.com",
    )
    with patch(
        "src.app.api.routes.admin_institutions.GoTrackerSubscriptionLifecycleService",
        return_value=svc,
    ):
        result = await _ensure_gotracker_webhook_after_location_save(
            session,
            institution=institution,
            location=location,
        )

    assert result is row
    svc.ensure_location_subscription.assert_awaited_once_with(
        institution=institution,
        location=location,
        callback_url="https://api.example.com/api/v1/gotracker/webhooks/loc-1",
    )


@pytest.mark.asyncio
async def test_location_save_skips_reconcile_for_non_gotracker_location():
    session = _session_without_subscription()
    institution = SimpleNamespace(id="inst-1", pms_type="nexhealth")
    location = SimpleNamespace(
        id="loc-1",
        gotracker_product_key_encrypted="encrypted-key",
    )

    with patch(
        "src.app.api.routes.admin_institutions.GoTrackerSubscriptionLifecycleService"
    ) as service_cls:
        result = await _ensure_gotracker_webhook_after_location_save(
            session,
            institution=institution,
            location=location,
        )

    assert result is None
    service_cls.assert_not_called()
