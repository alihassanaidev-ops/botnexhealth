"""
Unit tests for SyncService — sync orchestrators, SyncResult, and error handling.

Uses mocked adapter + mocked DB session to test sync logic without a database.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.app.pms.base import PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking
from src.app.pms.models import (
    UniversalAppointmentType,
    UniversalOperatory,
    UniversalProvider,
)
from src.app.services.sync_service import SyncResult, SyncService


# =============================================================================
# SyncResult Tests
# =============================================================================


class TestSyncResult:
    """Test SyncResult dataclass."""

    def test_default_values(self):
        r = SyncResult(location_slug="main-office")
        assert r.location_slug == "main-office"
        assert r.providers_synced == 0
        assert r.appointment_types_synced == 0
        assert r.operatories_synced == 0
        assert r.descriptors_synced == 0
        assert r.errors == []

    def test_success_when_no_errors(self):
        r = SyncResult(location_slug="office1", providers_synced=5)
        assert r.success is True

    def test_not_success_when_errors(self):
        r = SyncResult(location_slug="office1", errors=["Provider sync error: timeout"])
        assert r.success is False

    def test_errors_list_independence(self):
        """Ensure default errors list is independent per instance."""
        r1 = SyncResult(location_slug="a")
        r2 = SyncResult(location_slug="b")
        r1.errors.append("fail")
        assert len(r2.errors) == 0


# =============================================================================
# Mock Adapter Factories
# =============================================================================


def _make_base_adapter(source: str = "nexhealth") -> AsyncMock:
    """Create a mock PMSAdapter (no optional capabilities)."""
    adapter = AsyncMock(spec=PMSAdapter)
    adapter.source = source
    adapter.list_providers = AsyncMock(return_value=[])
    adapter.list_appointment_types = AsyncMock(return_value=[])
    adapter.list_operatories = AsyncMock(return_value=[])
    return adapter


def _make_full_adapter(source: str = "nexhealth") -> MagicMock:
    """Create a mock adapter implementing all 3 ABCs."""

    class _FullAdapter(PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking):
        pass

    adapter = MagicMock(spec=_FullAdapter)
    adapter.source = source
    adapter.list_providers = AsyncMock(return_value=[])
    adapter.list_appointment_types = AsyncMock(return_value=[])
    adapter.list_operatories = AsyncMock(return_value=[])
    adapter.list_pms_descriptors = AsyncMock(return_value=[])
    adapter.list_availabilities = AsyncMock(return_value=[])
    return adapter


def _sample_providers() -> list[UniversalProvider]:
    return [
        UniversalProvider(
            id="nh-10", source="nexhealth", name="Dr. Smith",
            first_name="John", last_name="Smith", specialty="General",
        ),
        UniversalProvider(
            id="nh-11", source="nexhealth", name="Dr. Jones",
            first_name="Jane", last_name="Jones", specialty="Pediatric",
        ),
    ]


def _sample_appointment_types() -> list[UniversalAppointmentType]:
    return [
        UniversalAppointmentType(
            id="nh-50", source="nexhealth", name="Cleaning",
            duration_minutes=30, source_id="50", source_metadata={},
        ),
    ]


def _sample_operatories() -> list[UniversalOperatory]:
    return [
        UniversalOperatory(id="nh-100", source="nexhealth", name="Chair 1", is_active=True),
        UniversalOperatory(id="nh-101", source="nexhealth", name="Chair 2", is_active=False),
    ]


def _sample_descriptors() -> list[dict]:
    return [
        {"id": 500, "name": "Prophylaxis Adult", "code": "D1110", "active": True, "descriptor_type": "Procedure Code"},
        {"id": 501, "name": "Crown", "code": "D2750", "active": True},
    ]




# =============================================================================
# Sync Orchestrator Tests
# =============================================================================


class TestSyncProviders:
    """Test _sync_providers orchestrator."""

    @pytest.mark.asyncio
    async def test_sync_providers_success(self):
        adapter = _make_base_adapter()
        adapter.list_providers.return_value = _sample_providers()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()

        svc = SyncService(session)
        result = SyncResult(location_slug="test")
        now = datetime.now(timezone.utc)

        await svc._sync_providers(adapter, "t1", "l1", now, result)

        assert result.providers_synced == 2
        assert len(result.errors) == 0
        adapter.list_providers.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_providers_error_captured(self):
        adapter = _make_base_adapter()
        adapter.list_providers.side_effect = RuntimeError("API timeout")

        session = AsyncMock()
        svc = SyncService(session)
        result = SyncResult(location_slug="test")
        now = datetime.now(timezone.utc)

        await svc._sync_providers(adapter, "t1", "l1", now, result)

        assert result.providers_synced == 0
        assert len(result.errors) == 1
        assert "Provider sync error" in result.errors[0]


class TestSyncAppointmentTypes:
    """Test _sync_appointment_types orchestrator."""

    @pytest.mark.asyncio
    async def test_sync_appointment_types_success(self):
        adapter = _make_base_adapter()
        adapter.list_appointment_types.return_value = _sample_appointment_types()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()

        svc = SyncService(session)
        result = SyncResult(location_slug="test")
        now = datetime.now(timezone.utc)

        await svc._sync_appointment_types(adapter, "t1", "l1", now, result)

        assert result.appointment_types_synced == 1
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_sync_appointment_types_error_captured(self):
        adapter = _make_base_adapter()
        adapter.list_appointment_types.side_effect = Exception("Connection reset")

        session = AsyncMock()
        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_appointment_types(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.appointment_types_synced == 0
        assert "Appointment type sync error" in result.errors[0]


class TestSyncOperatories:
    """Test _sync_operatories orchestrator."""

    @pytest.mark.asyncio
    async def test_sync_operatories_success(self):
        adapter = _make_base_adapter()
        adapter.list_operatories.return_value = _sample_operatories()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()

        svc = SyncService(session)
        result = SyncResult(location_slug="test")
        now = datetime.now(timezone.utc)

        await svc._sync_operatories(adapter, "t1", "l1", now, result)

        assert result.operatories_synced == 2
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_sync_operatories_error_captured(self):
        adapter = _make_base_adapter()
        adapter.list_operatories.side_effect = Exception("NexHealth 500")

        session = AsyncMock()
        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_operatories(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.operatories_synced == 0
        assert "Operatory sync error" in result.errors[0]


class TestSyncDescriptors:
    """Test _sync_descriptors orchestrator."""

    @pytest.mark.asyncio
    async def test_sync_descriptors_skipped_if_not_supported(self):
        """Base adapter (no SupportsAppointmentTypeCreation) should skip descriptors."""
        adapter = _make_base_adapter()

        session = AsyncMock()
        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_descriptors(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.descriptors_synced == 0
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_sync_descriptors_success(self):
        adapter = _make_full_adapter()
        adapter.list_pms_descriptors.return_value = _sample_descriptors()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()

        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_descriptors(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.descriptors_synced == 2
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_sync_descriptors_skips_empty_id(self):
        """Descriptors without an 'id' should be skipped."""
        adapter = _make_full_adapter()
        adapter.list_pms_descriptors.return_value = [
            {"name": "No ID Descriptor"},  # missing id
            {"id": 500, "name": "Valid", "code": "D1110", "active": True},
        ]

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()

        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_descriptors(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.descriptors_synced == 1

    @pytest.mark.asyncio
    async def test_sync_descriptors_error_captured(self):
        adapter = _make_full_adapter()
        adapter.list_pms_descriptors.side_effect = Exception("Timeout")

        session = AsyncMock()
        svc = SyncService(session)
        result = SyncResult(location_slug="test")

        await svc._sync_descriptors(adapter, "t1", "l1", datetime.now(timezone.utc), result)

        assert result.descriptors_synced == 0
        assert "Descriptor sync error" in result.errors[0]



# =============================================================================
# Full sync_location Tests
# =============================================================================


class TestSyncLocation:
    """Test sync_location end-to-end with mocks."""

    @pytest.mark.asyncio
    async def test_sync_location_adapter_failure(self):
        """If get_adapter fails, result has error and returns early."""
        session = AsyncMock()
        session.flush = AsyncMock()
        svc = SyncService(session)

        tenant = MagicMock()
        tenant.id = "t1"
        location = MagicMock()
        location.id = "l1"
        location.slug = "main-office"

        with patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            side_effect=Exception("No API key"),
        ), patch("src.app.services.sync_service.log_audit_background"):
            result = await svc.sync_location(tenant, location)

        assert result.success is False
        assert "Failed to get PMS adapter" in result.errors[0]
        assert result.providers_synced == 0

    @pytest.mark.asyncio
    async def test_sync_location_all_entities(self):
        """Full sync with a full adapter returns counts for all entity types."""
        adapter = _make_full_adapter()
        adapter.list_providers.return_value = _sample_providers()
        adapter.list_appointment_types.return_value = _sample_appointment_types()
        adapter.list_operatories.return_value = _sample_operatories()
        adapter.list_pms_descriptors.return_value = _sample_descriptors()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()
        session.flush = AsyncMock()

        svc = SyncService(session)

        tenant = MagicMock()
        tenant.id = "t1"
        location = MagicMock()
        location.id = "l1"
        location.slug = "main"

        with patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            return_value=adapter,
        ), patch("src.app.services.sync_service.log_audit_background"):
            result = await svc.sync_location(tenant, location)

        assert result.success is True
        assert result.providers_synced == 2
        assert result.appointment_types_synced == 1
        assert result.operatories_synced == 2
        assert result.descriptors_synced == 2

    @pytest.mark.asyncio
    async def test_sync_location_partial_errors(self):
        """If one entity fails, others still sync and error is captured."""
        adapter = _make_full_adapter()
        adapter.list_providers.side_effect = Exception("Provider API down")
        adapter.list_appointment_types.return_value = _sample_appointment_types()
        adapter.list_operatories.return_value = _sample_operatories()
        adapter.list_pms_descriptors.return_value = _sample_descriptors()

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()
        session.flush = AsyncMock()

        svc = SyncService(session)

        tenant = MagicMock()
        tenant.id = "t1"
        location = MagicMock()
        location.id = "l1"
        location.slug = "main"

        with patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            return_value=adapter,
        ), patch("src.app.services.sync_service.log_audit_background"):
            result = await svc.sync_location(tenant, location)

        assert result.success is False
        assert result.providers_synced == 0
        assert result.appointment_types_synced == 1
        assert result.operatories_synced == 2
        assert "Provider sync error" in result.errors[0]


class TestSyncAllLocations:
    """Test sync_all_locations iterates active locations only."""

    @pytest.mark.asyncio
    async def test_skips_inactive_locations(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()
        session.flush = AsyncMock()

        svc = SyncService(session)

        tenant = MagicMock()
        tenant.id = "t1"

        active_loc = MagicMock()
        active_loc.id = "l1"
        active_loc.slug = "active"
        active_loc.is_active = True

        inactive_loc = MagicMock()
        inactive_loc.id = "l2"
        inactive_loc.slug = "closed"
        inactive_loc.is_active = False

        adapter = _make_base_adapter()

        with patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            return_value=adapter,
        ), patch("src.app.services.sync_service.log_audit_background"):
            results = await svc.sync_all_locations(tenant, [active_loc, inactive_loc])

        assert "active" in results
        assert "closed" not in results
