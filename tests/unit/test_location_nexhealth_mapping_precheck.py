"""Creating or editing a location must explain a mapping conflict up front.

The unique index can only reject the write; it cannot say which location is
holding the practice-software site, nor that the holder is a deleted row the
operator can reactivate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.app.api.routes.admin_institutions import (
    _assert_nexhealth_mapping_available,
)


def _service(holder: SimpleNamespace | None) -> AsyncMock:
    service = AsyncMock()
    service.find_location_by_nexhealth_mapping = AsyncMock(return_value=holder)
    return service


def _holder(*, is_active: bool, slug: str = "relaxation-dental") -> SimpleNamespace:
    return SimpleNamespace(
        id="loc-1",
        slug=slug,
        is_active=is_active,
        institution_id="inst-1",
    )


@pytest.mark.asyncio
async def test_free_mapping_passes() -> None:
    service = _service(None)
    await _assert_nexhealth_mapping_available(
        service, subdomain="silora-demo-practice", nexhealth_location_id="358579"
    )
    service.find_location_by_nexhealth_mapping.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subdomain", "location_id"),
    [(None, "358579"), ("silora-demo-practice", None), (None, None), ("", "")],
)
async def test_partial_mapping_is_not_checked(subdomain, location_id) -> None:
    """The index only covers rows where both halves are set."""
    service = _service(_holder(is_active=True))
    await _assert_nexhealth_mapping_available(
        service, subdomain=subdomain, nexhealth_location_id=location_id
    )
    service.find_location_by_nexhealth_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_holder_conflict_offers_a_way_forward() -> None:
    service = _service(_holder(is_active=False))

    with pytest.raises(HTTPException) as caught:
        await _assert_nexhealth_mapping_available(
            service, subdomain="silora-demo-practice", nexhealth_location_id="358579"
        )

    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert "358579" in detail
    assert "relaxation-dental" in detail
    assert "deleted" in detail
    assert "Reactivate" in detail


@pytest.mark.asyncio
async def test_active_holder_conflict_omits_the_recovery_hint() -> None:
    service = _service(_holder(is_active=True))

    with pytest.raises(HTTPException) as caught:
        await _assert_nexhealth_mapping_available(
            service, subdomain="silora-demo-practice", nexhealth_location_id="358579"
        )

    detail = caught.value.detail
    assert "active" in detail
    assert "Reactivate" not in detail


@pytest.mark.asyncio
async def test_update_excludes_the_location_being_edited() -> None:
    """Re-saving a location unchanged must not collide with its own mapping."""
    service = _service(None)

    await _assert_nexhealth_mapping_available(
        service,
        subdomain="silora-demo-practice",
        nexhealth_location_id="358579",
        exclude_location_id="loc-1",
    )

    _, kwargs = service.find_location_by_nexhealth_mapping.await_args
    assert kwargs["exclude_location_id"] == "loc-1"
