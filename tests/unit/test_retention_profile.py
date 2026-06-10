"""Per-tenant retention profile resolution + window overrides."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.app.services.retention_policy import (
    clinical_record_retain_until,
    default_recording_retain_until,
    global_retention_profile,
    retention_profile_for,
)


def _inst(clinical=None, recording=None):
    return SimpleNamespace(
        retention_clinical_record_days=clinical,
        retention_recording_days=recording,
    )


def test_no_overrides_uses_global_defaults():
    profile = retention_profile_for(_inst())
    glob = global_retention_profile()
    assert profile.clinical_record_days == glob.clinical_record_days
    assert profile.recording_days == glob.recording_days
    assert profile.apply_minor_extension is True


def test_per_institution_override_wins():
    profile = retention_profile_for(_inst(clinical=14, recording=7))
    assert profile.clinical_record_days == 14
    assert profile.recording_days == 7


def test_partial_override_keeps_global_for_the_rest():
    profile = retention_profile_for(_inst(recording=30))
    glob = global_retention_profile()
    assert profile.clinical_record_days == glob.clinical_record_days
    assert profile.recording_days == 30


def test_clinical_record_window_respects_days_and_skips_minor_extension():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # With a minor DOB but extension disabled, the deadline is exactly +90d,
    # not extended to age 28.
    deadline = clinical_record_retain_until(
        created, date_of_birth="2020-01-01", days=90, apply_minor_extension=False
    )
    assert deadline == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_recording_window_respects_days_override():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert default_recording_retain_until(created, days=30) == datetime(
        2026, 1, 31, tzinfo=timezone.utc
    )


def test_contact_anonymization_cutoff_is_per_tenant():
    """The anonymization sweep must age contacts out on their institution's
    own clinical-record window (per-tenant), not the global 10-year clock."""
    from src.app.services.retention_policy import build_contact_anonymize_update

    stmt = build_contact_anonymize_update(datetime(2026, 5, 30, tzinfo=timezone.utc))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    # Joins institutions and applies the per-row override interval.
    assert "make_interval" in sql
    assert "retention_clinical_record_days" in sql
    assert "institutions" in sql
