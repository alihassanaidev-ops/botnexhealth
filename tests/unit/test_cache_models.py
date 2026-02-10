"""
Unit tests for cache models: TenantOperatory, TenantDescriptor, TenantAvailability.

Tests model creation and field defaults without requiring a database.
"""

from src.app.models.tenant_operatory import TenantOperatory
from src.app.models.tenant_descriptor import TenantDescriptor
from src.app.models.tenant_availability import TenantAvailability


class TestTenantOperatoryModel:
    def test_create_operatory(self):
        op = TenantOperatory(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="nh-100",
            name="Chair 1",
            is_active=True,
        )
        assert op.name == "Chair 1"
        assert op.source == "nexhealth"
        assert op.source_id == "nh-100"
        assert op.is_active is True

    def test_operatory_repr(self):
        op = TenantOperatory(
            id="abc",
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="100",
            name="Chair 2",
        )
        assert "Chair 2" in repr(op)
        assert "nexhealth" in repr(op)

    def test_operatory_inactive(self):
        op = TenantOperatory(
            tenant_id="t1",
            location_id="l1",
            source="sikka",
            source_id="sk-50",
            name="Room A",
            is_active=False,
        )
        assert op.is_active is False


class TestTenantDescriptorModel:
    def test_create_descriptor(self):
        d = TenantDescriptor(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="500",
            name="Prophylaxis Adult",
            descriptor_type="Procedure Code",
            code="D1110",
            is_active=True,
        )
        assert d.name == "Prophylaxis Adult"
        assert d.code == "D1110"
        assert d.descriptor_type == "Procedure Code"

    def test_descriptor_with_metadata(self):
        d = TenantDescriptor(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="501",
            name="Crown",
            code="D2750",
            source_metadata={"foreign_id": "abc", "foreign_id_type": "dentrix"},
        )
        assert d.source_metadata["foreign_id"] == "abc"

    def test_descriptor_repr(self):
        d = TenantDescriptor(
            id="def",
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="502",
            name="Exam",
            code="D0150",
        )
        assert "Exam" in repr(d)
        assert "D0150" in repr(d)


class TestTenantAvailabilityModel:
    def test_create_availability(self):
        av = TenantAvailability(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="200",
            provider_source_id="10",
            provider_name="Dr. Smith",
            begin_time="09:00",
            end_time="17:00",
            days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            active=True,
            synced=False,
        )
        assert av.begin_time == "09:00"
        assert av.end_time == "17:00"
        assert "Monday" in av.days
        assert av.active is True
        assert av.synced is False

    def test_availability_with_appointment_types(self):
        av = TenantAvailability(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="201",
            provider_source_id="10",
            appointment_type_ids=["5", "6", "7"],
            appointment_type_names=["Cleaning", "Exam", "Crown"],
            active=True,
        )
        assert len(av.appointment_type_ids) == 3
        assert av.appointment_type_names[0] == "Cleaning"

    def test_availability_specific_date(self):
        av = TenantAvailability(
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="202",
            provider_source_id="10",
            specific_date="2026-03-15",
            begin_time="13:00",
            end_time="16:00",
            active=True,
        )
        assert av.specific_date == "2026-03-15"
        assert av.days is None

    def test_availability_repr(self):
        av = TenantAvailability(
            id="xyz",
            tenant_id="t1",
            location_id="l1",
            source="nexhealth",
            source_id="203",
            provider_name="Dr. Jones",
            begin_time="08:00",
            end_time="12:00",
        )
        assert "Dr. Jones" in repr(av)
        assert "08:00-12:00" in repr(av)
