"""
Unit tests for tenant_setup route schemas (request + response models).

Tests Pydantic validation, defaults, and from_attributes serialization.
"""

import pytest
from pydantic import ValidationError

from src.app.api.routes.tenant_setup import (
    CachedAppointmentTypeResponse,
    CachedAvailabilityResponse,
    CachedDescriptorResponse,
    CachedOperatoryResponse,
    CachedProviderResponse,
    CreateAppointmentTypeRequest,
    LocationInfoResponse,
    SetupOverviewResponse,
    UpdateAppointmentTypeRequest,
    UpdateAvailabilityRequest,
)


# =============================================================================
# Response Schemas
# =============================================================================


class TestCachedProviderResponse:
    def test_full_fields(self):
        p = CachedProviderResponse(
            id="uuid-1",
            source_id="nh-10",
            source="nexhealth",
            name="Dr. Smith",
            first_name="John",
            last_name="Smith",
            specialty="General",
            is_active=True,
            synced_at="2026-01-01T00:00:00Z",
        )
        assert p.name == "Dr. Smith"
        assert p.source == "nexhealth"

    def test_minimal_fields(self):
        p = CachedProviderResponse(id="uuid-1", source_id="10", source="sikka")
        assert p.name is None
        assert p.is_active is True
        assert p.synced_at is None

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            CachedProviderResponse(id="uuid-1", source_id="10")  # missing source


class TestCachedAppointmentTypeResponse:
    def test_full_fields(self):
        at = CachedAppointmentTypeResponse(
            id="uuid-2",
            source_id="nh-50",
            source="nexhealth",
            name="Cleaning",
            duration_minutes=30,
            source_metadata={"descriptor_ids": ["500", "501"]},
            is_active=True,
        )
        assert at.name == "Cleaning"
        assert at.duration_minutes == 30
        assert at.source_metadata["descriptor_ids"] == ["500", "501"]

    def test_minimal_fields(self):
        at = CachedAppointmentTypeResponse(
            id="uuid-2", source_id="50", source="nexhealth", name="Exam",
        )
        assert at.duration_minutes is None
        assert at.source_metadata is None

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            CachedAppointmentTypeResponse(id="uuid-2", source_id="50", source="nexhealth")


class TestCachedOperatoryResponse:
    def test_full_fields(self):
        op = CachedOperatoryResponse(
            id="uuid-3", source_id="nh-100", source="nexhealth",
            name="Chair 1", is_active=True,
        )
        assert op.name == "Chair 1"

    def test_inactive(self):
        op = CachedOperatoryResponse(
            id="uuid-3", source_id="100", source="nexhealth",
            name="Room B", is_active=False,
        )
        assert op.is_active is False


class TestCachedDescriptorResponse:
    def test_full_fields(self):
        d = CachedDescriptorResponse(
            id="uuid-4", source_id="500", source="nexhealth",
            name="Prophylaxis Adult", descriptor_type="Procedure Code",
            code="D1110", is_active=True,
            source_metadata={"foreign_id": "abc"},
        )
        assert d.code == "D1110"
        assert d.descriptor_type == "Procedure Code"

    def test_minimal_fields(self):
        d = CachedDescriptorResponse(
            id="uuid-4", source_id="500", source="nexhealth", name="Exam",
        )
        assert d.code is None
        assert d.descriptor_type is None
        assert d.source_metadata is None


class TestCachedAvailabilityResponse:
    def test_full_fields(self):
        av = CachedAvailabilityResponse(
            id="uuid-5", source_id="200", source="nexhealth",
            provider_source_id="10", provider_name="Dr. Smith",
            operatory_source_id="100", operatory_name="Chair 1",
            begin_time="09:00", end_time="17:00",
            days=["Monday", "Tuesday"],
            appointment_type_ids=["50", "51"],
            appointment_type_names=["Cleaning", "Exam"],
            active=True, synced=True,
        )
        assert av.begin_time == "09:00"
        assert av.days == ["Monday", "Tuesday"]
        assert av.appointment_type_ids == ["50", "51"]

    def test_minimal_fields(self):
        av = CachedAvailabilityResponse(
            id="uuid-5", source_id="200", source="nexhealth",
        )
        assert av.provider_source_id is None
        assert av.days is None
        assert av.appointment_type_ids is None
        assert av.active is True
        assert av.synced is False

    def test_specific_date_fields(self):
        av = CachedAvailabilityResponse(
            id="uuid-5", source_id="201", source="nexhealth",
            specific_date="2026-03-15", begin_time="13:00", end_time="16:00",
        )
        assert av.specific_date == "2026-03-15"
        assert av.days is None


class TestLocationInfoResponse:
    def test_full_fields(self):
        loc = LocationInfoResponse(
            id="uuid-6", name="Main Office", slug="main-office",
            nexhealth_subdomain="test-sub", nexhealth_location_id="123",
        )
        assert loc.slug == "main-office"
        assert loc.nexhealth_subdomain == "test-sub"

    def test_minimal_fields(self):
        loc = LocationInfoResponse(id="uuid-6", name="Branch", slug="branch")
        assert loc.nexhealth_subdomain is None
        assert loc.nexhealth_location_id is None


class TestSetupOverviewResponse:
    def test_full_fields(self):
        overview = SetupOverviewResponse(
            location=LocationInfoResponse(id="l1", name="Main", slug="main"),
            pms_source="nexhealth",
            can_create_appointment_types=True,
            can_link_availability=True,
            counts={
                "providers": 5,
                "appointment_types": 3,
                "operatories": 2,
                "descriptors": 10,
                "availabilities": 8,
            },
        )
        assert overview.pms_source == "nexhealth"
        assert overview.can_create_appointment_types is True
        assert overview.counts["providers"] == 5

    def test_defaults(self):
        overview = SetupOverviewResponse(
            location=LocationInfoResponse(id="l1", name="A", slug="a"),
        )
        assert overview.pms_source is None
        assert overview.can_create_appointment_types is False
        assert overview.can_link_availability is False
        assert overview.counts == {}


# =============================================================================
# Request Schemas
# =============================================================================


class TestCreateAppointmentTypeRequest:
    def test_full_request(self):
        req = CreateAppointmentTypeRequest(
            name="Adult Cleaning",
            duration_minutes=45,
            descriptor_ids=["500", "501"],
        )
        assert req.name == "Adult Cleaning"
        assert req.duration_minutes == 45
        assert len(req.descriptor_ids) == 2

    def test_default_descriptor_ids(self):
        req = CreateAppointmentTypeRequest(name="Exam", duration_minutes=30)
        assert req.descriptor_ids == []

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(duration_minutes=30)

    def test_missing_duration_raises(self):
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="Test")


class TestUpdateAppointmentTypeRequest:
    def test_all_none_defaults(self):
        req = UpdateAppointmentTypeRequest()
        assert req.name is None
        assert req.duration_minutes is None
        assert req.descriptor_ids is None

    def test_partial_update(self):
        req = UpdateAppointmentTypeRequest(name="Updated Name")
        assert req.name == "Updated Name"
        assert req.duration_minutes is None


class TestUpdateAvailabilityRequest:
    def test_all_none_defaults(self):
        req = UpdateAvailabilityRequest()
        assert req.appointment_type_ids is None
        assert req.days is None
        assert req.start_time is None
        assert req.end_time is None
        assert req.operatory_id is None
        assert req.active is None

    def test_link_appointment_types(self):
        req = UpdateAvailabilityRequest(appointment_type_ids=["50", "51"])
        assert req.appointment_type_ids == ["50", "51"]

    def test_update_schedule(self):
        req = UpdateAvailabilityRequest(
            days=["Monday", "Wednesday", "Friday"],
            start_time="08:00",
            end_time="15:00",
        )
        assert req.days == ["Monday", "Wednesday", "Friday"]
        assert req.start_time == "08:00"

    def test_deactivate(self):
        req = UpdateAvailabilityRequest(active=False)
        assert req.active is False
