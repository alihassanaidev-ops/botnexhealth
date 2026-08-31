"""Item 21 · somewhere to put an inbound sales enquiry.

Sales Qualification starts with someone who is not a patient yet, and there was
nowhere to record them, which blocked Item 24 entirely. Their details belong to
a person who has consented to nothing, so they get patient-grade treatment.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from src.app.database import Base
from src.app.models.campaign_enquiry import CampaignEnquiry, EnquiryStatus

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260830_campaign_enquiries.py"
TABLE = Base.metadata.tables["campaign_enquiries"]


class TestScoping:
    def test_an_enquiry_is_scoped_to_a_clinic_and_location(self):
        assert TABLE.c.institution_id.nullable is False
        assert "location_id" in TABLE.c

    def test_it_can_be_linked_to_the_run_handling_it(self):
        assert "workflow_run_id" in TABLE.c

    def test_it_can_point_at_an_existing_contact(self):
        """Conversion must match an existing patient, never duplicate one."""
        assert "contact_id" in TABLE.c


class TestIdempotency:
    def test_the_same_enquiry_twice_is_one_record(self):
        uniques = [
            c for c in TABLE.constraints if isinstance(c, UniqueConstraint)
        ]
        cols = {tuple(sorted(col.name for col in c.columns)) for c in uniques}
        assert ("institution_id", "intake_key") in cols

    def test_uniqueness_is_per_institution(self):
        """Two clinics must be able to receive the same key from their forms."""
        unique = next(
            c for c in TABLE.constraints if isinstance(c, UniqueConstraint)
        )
        assert "institution_id" in {col.name for col in unique.columns}


class TestPersonalDetails:
    def test_email_and_phone_are_stored_encrypted(self):
        """The columns are the encrypted ones — there is no plaintext column."""
        assert "email_encrypted" in TABLE.c
        assert "phone_encrypted" in TABLE.c
        assert "email" not in TABLE.c
        assert "phone" not in TABLE.c

    def test_setting_a_phone_keeps_the_hash_in_step(self):
        """Otherwise the hash and the encrypted value can silently disagree."""
        enquiry = CampaignEnquiry()
        enquiry.phone = "+14165551234"
        assert enquiry.phone_encrypted is not None
        assert enquiry.phone_hash is not None
        assert enquiry.phone_encrypted != "+14165551234", "must not be plaintext"
        assert enquiry.phone == "+14165551234", "must decrypt back"

    def test_clearing_a_phone_clears_its_hash(self):
        enquiry = CampaignEnquiry()
        enquiry.phone = "+14165551234"
        enquiry.phone = None
        assert enquiry.phone_hash is None

    def test_email_round_trips_without_being_stored_in_the_clear(self):
        enquiry = CampaignEnquiry()
        enquiry.email = "someone@example.com"
        assert enquiry.email_encrypted != "someone@example.com"
        assert enquiry.email == "someone@example.com"


class TestStatus:
    def test_status_values_are_constrained(self):
        checks = [c for c in TABLE.constraints if isinstance(c, CheckConstraint)]
        migration = MIGRATION.read_text()
        for member in EnquiryStatus:
            assert f"'{member.value}'" in migration, member.value

    def test_a_new_enquiry_starts_as_new(self):
        assert TABLE.c.status.default.arg == EnquiryStatus.NEW.value


class TestIsolation:
    """Cross-clinic reads are the most serious defect class in this product."""

    def test_the_migration_turns_row_level_security_on(self):
        migration = MIGRATION.read_text()
        assert "ENABLE ROW LEVEL SECURITY" in migration
        assert "FORCE ROW LEVEL SECURITY" in migration

    def test_the_policy_is_scoped_by_institution(self):
        migration = MIGRATION.read_text()
        assert "app_rls_institution_id()" in migration
        assert "CREATE POLICY" in migration

    def test_a_location_bound_user_is_held_to_their_location(self):
        migration = MIGRATION.read_text()
        assert "app_rls_location_id()" in migration

    def test_the_policy_covers_writes_as_well_as_reads(self):
        """USING alone would let a clinic insert into another clinic's scope."""
        migration = MIGRATION.read_text()
        assert "WITH CHECK" in migration
