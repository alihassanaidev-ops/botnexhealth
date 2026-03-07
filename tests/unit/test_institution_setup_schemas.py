"""
Unit tests for institution_setup route schemas.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.api.routes.institution_setup import (
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


class TestCachedProviderResponse:
    def test_minimal_fields(self):
        p = CachedProviderResponse(id="uuid-1", source_id="10")
        assert p.name is None
        assert p.is_active is True

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            CachedProviderResponse(id="uuid-1")  # missing source_id


class TestCachedAppointmentTypeResponse:
    def test_full_fields(self):
        at = CachedAppointmentTypeResponse(
            id="uuid-2",
            source_id="50",
            name="Cleaning",
            duration_minutes=30,
            source_metadata={"descriptor_ids": ["500", "501"]},
            is_active=True,
        )
        assert at.name == "Cleaning"
        assert at.duration_minutes == 30

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            CachedAppointmentTypeResponse(id="uuid-2", source_id="50")


class TestCachedOperatoryResponse:
    def test_minimal_fields(self):
        op = CachedOperatoryResponse(id="uuid-3", source_id="100", name="Chair 1")
        assert op.name == "Chair 1"
        assert op.is_active is True


class TestCachedDescriptorResponse:
    def test_minimal_fields(self):
        d = CachedDescriptorResponse(id="uuid-4", source_id="500", name="Exam")
        assert d.code is None
        assert d.descriptor_type is None


class TestCachedAvailabilityResponse:
    def test_full_fields(self):
        av = CachedAvailabilityResponse(
            id="uuid-5",
            source_id="200",
            provider_source_id="10",
            begin_time="09:00",
            end_time="17:00",
            days=["Monday", "Tuesday"],
            appointment_type_ids=["50", "51"],
            active=True,
            synced=True,
        )
        assert av.provider_source_id == "10"
        assert av.days == ["Monday", "Tuesday"]
        assert av.synced is True

    def test_defaults(self):
        av = CachedAvailabilityResponse(id="uuid-5", source_id="200")
        assert av.active is True
        assert av.synced is False


class TestLocationInfoResponse:
    def test_minimal_fields(self):
        loc = LocationInfoResponse(id="uuid-6", name="Main Office", slug="main-office")
        assert loc.slug == "main-office"


class TestSetupOverviewResponse:
    def test_defaults(self):
        overview = SetupOverviewResponse(
            location=LocationInfoResponse(id="l1", name="Main", slug="main"),
        )
        assert overview.pms_source is None
        assert overview.can_create_appointment_types is False
        assert overview.can_link_availability is False
        assert overview.counts == {}


class TestCreateAppointmentTypeRequest:
    def test_defaults(self):
        req = CreateAppointmentTypeRequest(name="Exam", duration_minutes=30)
        assert req.descriptor_ids == []


class TestUpdateAppointmentTypeRequest:
    def test_partial_update(self):
        req = UpdateAppointmentTypeRequest(name="Updated Name")
        assert req.name == "Updated Name"
        assert req.duration_minutes is None


class TestUpdateAvailabilityRequest:
    def test_update_schedule(self):
        req = UpdateAvailabilityRequest(
            days=["Monday", "Wednesday"],
            start_time="08:00",
            end_time="15:00",
        )
        assert req.days == ["Monday", "Wednesday"]
        assert req.start_time == "08:00"
