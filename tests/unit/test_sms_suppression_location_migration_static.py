"""Static safety checks for location-scoped SMS suppression migration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260825_sms_suppress_location.py"


def test_location_suppression_migration_chains_after_current_head() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260825_sms_suppress_location"' in source
    assert 'down_revision = "20260825_twilio_sms_rls"' in source
    assert len("20260825_sms_suppress_location") <= 32


def test_location_suppression_migration_replaces_institution_wide_index() -> None:
    source = MIGRATION.read_text()

    assert "uq_sms_suppressions_active_institution_channel_phone" in source
    assert "uq_sms_suppressions_active_location_channel_phone" in source
    assert '"institution_id", "location_id", "channel", "phone_hash"' in source
