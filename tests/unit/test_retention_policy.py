from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from src.app.config import settings
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.services.retention_policy import (
    RetentionPolicyService,
    build_anonymized_contact_custom_field_delete,
    build_contact_anonymize_update,
    build_expired_call_phi_update,
    build_expired_dead_letter_raw_update,
    build_expired_notification_delete,
    build_expired_recording_select,
    build_expired_sms_body_update,
    build_purged_call_custom_field_delete,
    clinical_record_retain_until,
    retention_deadline,
    s3_bucket_key_from_recording_url,
)


def test_retention_deadline_normalizes_naive_datetimes_to_utc() -> None:
    created = datetime(2026, 5, 16, 12, 0, 0)

    assert retention_deadline(created, 90) == datetime(
        2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc
    )


def test_clinical_record_retention_defaults_to_ten_year_policy_window() -> None:
    created = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    assert clinical_record_retain_until(created) == created + timedelta(days=3650)


def test_minor_clinical_record_retention_extends_until_age_28() -> None:
    created = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    assert clinical_record_retain_until(
        created,
        date_of_birth=date(2020, 1, 2),
    ) == datetime(2048, 1, 2, 0, 0, 0, tzinfo=timezone.utc)


def test_recording_s3_url_parsing_is_bucket_scoped() -> None:
    url = (
        "https://nex-health-staging-recordings.s3.ca-central-1.amazonaws.com/"
        "recordings/inst/call.wav"
    )

    assert s3_bucket_key_from_recording_url(
        url,
        bucket="nex-health-staging-recordings",
        region="ca-central-1",
    ) == ("nex-health-staging-recordings", "recordings/inst/call.wav")
    assert (
        s3_bucket_key_from_recording_url(
            url,
            bucket="other-bucket",
            region="ca-central-1",
        )
        is None
    )


def test_sms_body_can_be_cleared_for_retention_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    log = SmsHistoryLog(
        from_number="+15550000000",
        to_number_hash="hash",
        to_number_masked="+*******1234",
        institution_location_id="11111111-1111-1111-1111-111111111111",
    )

    log.body = "Appointment reminder"
    assert log.body == "Appointment reminder"

    log.body = None
    assert log.body is None
    assert log.body_encrypted is None


def test_retention_sql_builders_include_legal_hold_and_purge_guards() -> None:
    now = datetime(2026, 5, 16, tzinfo=timezone.utc)

    sms_sql = _compile(build_expired_sms_body_update(now))
    assert "sms_history_logs.body_encrypted IS NOT NULL" in sms_sql
    assert "sms_history_logs.body_purged_at IS NULL" in sms_sql
    assert "sms_history_logs.body_retain_until <= " in sms_sql
    assert "sms_history_logs.legal_hold_until IS NULL" in sms_sql

    notification_sql = _compile(build_expired_notification_delete(now))
    assert "DELETE FROM notifications" in notification_sql
    assert "notifications.legal_hold_until IS NULL" in notification_sql

    call_sql = _compile(build_expired_call_phi_update(now))
    assert "UPDATE calls SET" in call_sql
    assert "transcript_with_tool_calls_encrypted" in call_sql
    assert "calls.purged_at IS NULL" in call_sql
    assert "calls.legal_hold_until IS NULL" in call_sql

    dlq_sql = _compile(build_expired_dead_letter_raw_update(now))
    assert "dead_letter_events.raw_payload_encrypted IS NOT NULL" in dlq_sql
    assert "dead_letter_events.raw_payload_purged_at IS NULL" in dlq_sql


# ── #3: recordings are owned exclusively by purge_expired_recordings ──────────


def test_call_phi_update_does_not_touch_recording_or_contact_fields() -> None:
    """The call-PHI purge must not clear recording_url/recording_deleted_at —
    that would orphan the S3 object — nor contact_id, which is kept so the
    call still links to its (separately anonymized) contact."""
    set_clause = _compile(build_expired_call_phi_update(_NOW)).split(" WHERE ")[0]

    assert "recording_url" not in set_clause
    assert "recording_deleted_at" not in set_clause
    assert "contact_id" not in set_clause
    # The PHI columns that SHOULD still be cleared.
    for column in (
        "transcript_with_tool_calls_encrypted",
        "summary_encrypted",
        "patient_intent",
        "next_action",
        "callback_note",
        "purged_at",
    ):
        assert column in set_clause


def test_expired_recording_select_covers_both_recording_and_record_clocks() -> None:
    """A recording is deleted when its own clock OR the parent call record's
    clock expires, so a medical-record recording is never orphaned."""
    sql = _compile(build_expired_recording_select(_NOW))

    assert "calls.recording_url IS NOT NULL" in sql
    assert "calls.recording_deleted_at IS NULL" in sql
    assert "calls.recording_retain_until <= " in sql
    assert "calls.retain_until <= " in sql
    # The two clocks are OR-ed, and legal hold still guards.
    assert " OR calls.retain_until <= " in sql
    assert "calls.legal_hold_until IS NULL" in sql


# ── #2: custom field values are purged with their parent records ─────────────


def test_purged_call_custom_field_delete_targets_call_entities() -> None:
    sql = _compile(build_purged_call_custom_field_delete(), literal_binds=True)

    assert "DELETE FROM custom_field_values" in sql
    assert "custom_field_values.entity_type = 'call'" in sql
    assert "SELECT calls.id" in sql
    assert "calls.purged_at IS NOT NULL" in sql


def test_anonymized_contact_custom_field_delete_targets_contact_entities() -> None:
    sql = _compile(build_anonymized_contact_custom_field_delete(), literal_binds=True)

    assert "DELETE FROM custom_field_values" in sql
    assert "custom_field_values.entity_type = 'contact'" in sql
    assert "SELECT contacts.id" in sql
    assert "contacts.anonymized_at IS NOT NULL" in sql


# ── #1: contact anonymization ────────────────────────────────────────────────


def test_contact_anonymize_strips_identity_for_fully_purged_contacts() -> None:
    sql = _compile(build_contact_anonymize_update(_NOW))
    set_clause, where_clause = sql.split(" WHERE ", 1)

    # Every identifying field is nulled and the marker is stamped.
    for column in (
        "first_name",
        "last_name",
        "full_name",
        "email_encrypted",
        "phone_encrypted",
        "date_of_birth_encrypted",
        "phone_hash",
        "anonymized_at",
    ):
        assert column in set_clause
    # nexhealth_patient_id is preserved for re-resolution + analytics.
    assert "nexhealth_patient_id" not in set_clause

    # Idempotent (skips already-anonymized) and activity-based: anonymize only
    # when no retained (non-purged) call exists for the contact.
    assert "contacts.anonymized_at IS NULL" in where_clause
    # Per-tenant cutoff: created_at + the institution's clinical window <= now,
    # joined to institutions (override column, global default fallback).
    assert "make_interval" in where_clause
    assert "institutions.retention_clinical_record_days" in where_clause
    assert "NOT (EXISTS" in where_clause
    assert "calls.contact_id = contacts.id" in where_clause
    assert "calls.purged_at IS NULL" in where_clause


def test_contact_anonymize_cutoff_uses_per_tenant_clinical_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cutoff is per-institution: created_at + the institution's clinical
    window <= now, where the window is the per-tenant override when set and the
    global default otherwise. So a freshly-created contact is never anonymized
    early, and a tenant on a shorter contractual window ages out sooner."""
    monkeypatch.setattr(settings, "retention_clinical_record_days", 3650)
    sql = _compile(build_contact_anonymize_update(_NOW), literal_binds=True)
    assert "make_interval" in sql
    # The override column is consulted first …
    assert "institutions.retention_clinical_record_days" in sql
    # … with the global default as the fallback.
    assert "3650" in sql
    # Compared against the literal "now".
    assert _NOW.date().isoformat() in sql


# ── apply(): full wiring + ordering ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_retention_policy_runs_every_step_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "aws_s3_bucket_name", "retention-test-bucket")
    monkeypatch.setattr(settings, "aws_region", "ca-central-1")
    call = _Call(
        id="call-1",
        recording_url=(
            "https://retention-test-bucket.s3.ca-central-1.amazonaws.com/"
            "recordings/inst/call-1.wav"
        ),
    )
    # One select (recordings) + eight counted statements.
    session = _FakeSession(calls=[call], rowcounts=[2, 3, 4, 5, 6, 7, 8, 9])
    s3 = _FakeS3()

    summary = await RetentionPolicyService(session, s3_client=s3).apply(_NOW)

    # S3 object deleted, then the DB reference cleared.
    assert s3.deleted == [
        {"Bucket": "retention-test-bucket", "Key": "recordings/inst/call-1.wav"}
    ]
    assert call.recording_url is None
    assert call.recording_deleted_at == _NOW
    assert session.committed is True
    assert summary == {
        "recordings_deleted": 1,
        "sms_bodies_purged": 2,
        "sms_rows_deleted": 3,
        "notifications_deleted": 4,
        "dead_letter_raw_payloads_purged": 5,
        "call_phi_purged": 6,
        "call_custom_fields_deleted": 7,
        "contacts_anonymized": 8,
        "contact_custom_fields_deleted": 9,
    }

    # The call-PHI update must run before the call custom-field delete, and
    # the contact anonymize before the contact custom-field delete.
    statements = [str(s) for s in session.executed]
    call_update = _index_of(statements, "UPDATE calls SET")
    call_cfv = _index_of(statements, "DELETE FROM custom_field_values")
    contact_update = _index_of(statements, "UPDATE contacts SET")
    assert call_update < call_cfv
    assert contact_update < _last_index_of(
        statements, "DELETE FROM custom_field_values"
    )


@pytest.mark.asyncio
async def test_apply_commits_even_when_nothing_expired() -> None:
    session = _FakeSession(calls=[], rowcounts=[0, 0, 0, 0, 0, 0, 0, 0])

    summary = await RetentionPolicyService(session, s3_client=None).apply(_NOW)

    assert session.committed is True
    assert all(value == 0 for value in summary.values())


@pytest.mark.asyncio
async def test_apply_clears_db_reference_when_s3_object_already_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash between an S3 delete and the DB commit re-runs next day; the
    second delete_object is a no-op and the DB reference is still cleared."""
    monkeypatch.setattr(settings, "aws_s3_bucket_name", "retention-test-bucket")
    monkeypatch.setattr(settings, "aws_region", "ca-central-1")
    call = _Call(
        id="call-1",
        recording_url=(
            "https://retention-test-bucket.s3.ca-central-1.amazonaws.com/"
            "recordings/inst/call-1.wav"
        ),
    )
    session = _FakeSession(calls=[call], rowcounts=[0] * 8)

    summary = await RetentionPolicyService(session, s3_client=_FakeS3()).apply(_NOW)

    assert summary["recordings_deleted"] == 1
    assert call.recording_url is None


_NOW = datetime(2026, 5, 16, tzinfo=timezone.utc)


def _compile(statement, *, literal_binds: bool = False) -> str:  # noqa: ANN001
    kwargs = {"literal_binds": True} if literal_binds else {}
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs=kwargs)
    )


def _index_of(statements: list[str], needle: str) -> int:
    for i, sql in enumerate(statements):
        if needle in sql:
            return i
    raise AssertionError(f"statement matching {needle!r} not found")


def _last_index_of(statements: list[str], needle: str) -> int:
    for i in range(len(statements) - 1, -1, -1):
        if needle in statements[i]:
            return i
    raise AssertionError(f"statement matching {needle!r} not found")


@dataclass
class _Call:
    id: str
    recording_url: str | None
    recording_deleted_at: datetime | None = None


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _SelectResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _ScalarResult(self.rows)


class _RowcountResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


@dataclass
class _FakeSession:
    """Minimal async session double.

    The first execute() is the recordings SELECT; every subsequent call is a
    counted UPDATE/DELETE whose rowcount is taken from ``rowcounts`` in order.
    """

    calls: list
    rowcounts: list
    executed: list = field(default_factory=list)
    committed: bool = False

    def __post_init__(self) -> None:
        self.rowcounts = list(self.rowcounts)

    async def execute(self, statement):
        self.executed.append(statement)
        if len(self.executed) == 1:
            return _SelectResult(self.calls)
        return _RowcountResult(self.rowcounts.pop(0))

    async def commit(self):
        self.committed = True


class _FakeS3:
    def __init__(self) -> None:
        self.deleted: list[dict[str, str]] = []

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
