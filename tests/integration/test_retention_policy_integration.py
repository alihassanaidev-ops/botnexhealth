"""End-to-end integration test for the PHI retention policy.

Runs ``RetentionPolicyService.apply()`` against a real Postgres database and
asserts the behavioural contract that the unit tests can only check at the
SQL-shape level:

  * expired call PHI is purged but the call row + contact link survive;
  * a recording is deleted once the *record* clock expires, even if its own
    (shorter) recording clock has not — the #3 orphan-prevention fix;
  * a contact is anonymized only when every linked call is purged;
  * a contact with any retained call — recent OR under legal hold — keeps its
    identity;
  * custom field values are deleted alongside their purged call / anonymized
    contact.

Skips when ``DATABASE_ADMIN_URL`` / ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.services.retention_policy import RetentionPolicyService

pytestmark = pytest.mark.integration

INSTITUTION_ID = "dcdcdcdc-dcdc-4cdc-8cdc-aaaaaaaaaaaa"
LOCATION_ID = "dcdcdcdc-dcdc-4cdc-8cdc-bbbbbbbbbbbb"
INSTITUTION_SLUG = "retention-policy-test"

NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(days=1)
FUTURE = NOW + timedelta(days=365)
LONG_AGO = NOW - timedelta(days=4015)  # ~11 years — older than the 10y window


def _admin_url() -> str | None:
    return os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = _admin_url()
    if not url:
        pytest.skip("DATABASE_ADMIN_URL/DATABASE_URL not set — skipping live test")
    return url


@pytest_asyncio.fixture
async def session(admin_url: str):
    engine = create_async_engine(admin_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for key, value in (
            ("app.context_type", "user"),
            ("app.role", "SUPER_ADMIN"),
            ("app.user_id", "00000000-0000-0000-0000-000000000001"),
        ):
            await s.execute(
                text("SELECT set_config(:k, :v, false)"), {"k": key, "v": value}
            )
        await _purge(s)
        yield s
        await _purge(s)
    await engine.dispose()


async def _purge(s: AsyncSession) -> None:
    for table, column in (
        ("custom_field_values", "institution_id"),
        ("calls", "institution_id"),
        ("contacts", "institution_id"),
        ("custom_field_definitions", "institution_id"),
        ("institution_locations", "institution_id"),
        ("institutions", "id"),
    ):
        await s.execute(
            text(f"DELETE FROM {table} WHERE {column} = :i"), {"i": INSTITUTION_ID}
        )
    await s.commit()


async def _seed_institution(s: AsyncSession) -> None:
    await s.execute(
        text(
            "INSERT INTO institutions (id, name, slug, is_active) "
            "VALUES (:id, 'Retention Test Clinic', :slug, true)"
        ),
        {"id": INSTITUTION_ID, "slug": INSTITUTION_SLUG},
    )
    await s.execute(
        text(
            "INSERT INTO institution_locations "
            "(id, institution_id, name, slug, is_active, timezone) "
            "VALUES (:id, :inst, 'Main', 'main', true, 'UTC')"
        ),
        {"id": LOCATION_ID, "inst": INSTITUTION_ID},
    )


async def _seed_contact(s: AsyncSession, contact_id: str, pms_id: str) -> None:
    await s.execute(
        text(
            """
            INSERT INTO contacts
              (id, institution_id, first_name, last_name, full_name,
               email_encrypted, phone_encrypted, date_of_birth_encrypted,
               phone_hash, nexhealth_patient_id, is_new_patient,
               created_at, updated_at)
            VALUES
              (:id, :inst, 'Jane', 'Doe', 'Jane Doe',
               'enc-email', 'enc-phone', 'enc-dob',
               :hash, :pms, false, :created, :created)
            """
        ),
        {
            "id": contact_id,
            "inst": INSTITUTION_ID,
            "hash": f"hash-{pms_id}",
            "pms": pms_id,
            "created": LONG_AGO,
        },
    )


async def _seed_call(
    s: AsyncSession,
    *,
    call_id: str,
    contact_id: str,
    retain_until: datetime,
    recording_retain_until: datetime | None,
    legal_hold_until: datetime | None = None,
    recording_url: str | None = None,
) -> None:
    await s.execute(
        text(
            """
            INSERT INTO calls
              (id, institution_id, location_id, contact_id, retell_call_id,
               call_status, summary_encrypted, transcript_with_tool_calls_encrypted,
               patient_intent, next_action, recording_url,
               retention_class, retain_until, recording_retain_until,
               legal_hold_until, is_new_patient, is_complaint,
               is_insurance_billing, callback_resolved, times_called,
               created_at, updated_at)
            VALUES
              (:id, :inst, :loc, :contact, :rcid,
               'appointment_booked', 'enc-summary', 'enc-transcript',
               'booking', 'follow up', :rec_url,
               'clinical_record', :retain, :rec_retain,
               :hold, false, false, false, false, 1, :created, :created)
            """
        ),
        {
            "id": call_id,
            "inst": INSTITUTION_ID,
            "loc": LOCATION_ID,
            "contact": contact_id,
            "rcid": f"retention-test-{call_id}",
            "rec_url": recording_url,
            "retain": retain_until,
            "rec_retain": recording_retain_until,
            "hold": legal_hold_until,
            "created": LONG_AGO,
        },
    )


async def _seed_custom_field_def(s: AsyncSession, def_id: str, entity_type: str) -> None:
    await s.execute(
        text(
            """
            INSERT INTO custom_field_definitions
              (id, institution_id, entity_type, field_name, field_key,
               field_type, is_phi, is_required, display_order, is_active,
               created_at)
            VALUES
              (:id, :inst, :etype, 'Patient Note', :key, 'text',
               true, false, 0, true, now())
            """
        ),
        {
            "id": def_id,
            "inst": INSTITUTION_ID,
            "etype": entity_type,
            "key": f"note_{entity_type}",
        },
    )


async def _seed_custom_field_value(
    s: AsyncSession, *, def_id: str, entity_type: str, entity_id: str
) -> str:
    value_id = str(uuid4())
    await s.execute(
        text(
            """
            INSERT INTO custom_field_values
              (id, institution_id, field_definition_id, entity_type, entity_id,
               value_encrypted, created_at, updated_at)
            VALUES
              (:id, :inst, :def, :etype, :eid, 'enc-value', now(), now())
            """
        ),
        {
            "id": value_id,
            "inst": INSTITUTION_ID,
            "def": def_id,
            "etype": entity_type,
            "eid": entity_id,
        },
    )
    return value_id


async def _scalar(s: AsyncSession, sql: str, **params):
    return (await s.execute(text(sql), params)).scalar()


@pytest.mark.asyncio
async def test_retention_policy_apply_end_to_end(session: AsyncSession) -> None:
    await _seed_institution(session)

    # Three contacts, each created ~11 years ago.
    contact_purged = str(uuid4())   # only call is expired -> anonymized
    contact_active = str(uuid4())   # has a recent retained call -> kept
    contact_held = str(uuid4())     # only call is under legal hold -> kept
    await _seed_contact(session, contact_purged, "pms-purged")
    await _seed_contact(session, contact_active, "pms-active")
    await _seed_contact(session, contact_held, "pms-held")

    # call_expired: record clock expired; recording clock is in the FUTURE —
    # the #3 fix means the recording must still be cleaned up.
    call_expired = str(uuid4())
    await _seed_call(
        session,
        call_id=call_expired,
        contact_id=contact_purged,
        retain_until=PAST,
        recording_retain_until=FUTURE,
        recording_url="https://example-bucket.s3.ca-central-1.amazonaws.com/r/x.wav",
    )
    # call_old: expired, belongs to the otherwise-active contact.
    call_old = str(uuid4())
    await _seed_call(
        session,
        call_id=call_old,
        contact_id=contact_active,
        retain_until=PAST,
        recording_retain_until=None,
    )
    # call_recent: not expired -> keeps contact_active alive.
    call_recent = str(uuid4())
    await _seed_call(
        session,
        call_id=call_recent,
        contact_id=contact_active,
        retain_until=FUTURE,
        recording_retain_until=None,
    )
    # call_held: expired by clock but under legal hold -> never purged.
    call_held = str(uuid4())
    await _seed_call(
        session,
        call_id=call_held,
        contact_id=contact_held,
        retain_until=PAST,
        recording_retain_until=None,
        legal_hold_until=FUTURE,
    )

    call_def = str(uuid4())
    contact_def = str(uuid4())
    await _seed_custom_field_def(session, call_def, "call")
    await _seed_custom_field_def(session, contact_def, "contact")
    cfv_expired_call = await _seed_custom_field_value(
        session, def_id=call_def, entity_type="call", entity_id=call_expired
    )
    cfv_recent_call = await _seed_custom_field_value(
        session, def_id=call_def, entity_type="call", entity_id=call_recent
    )
    cfv_purged_contact = await _seed_custom_field_value(
        session, def_id=contact_def, entity_type="contact", entity_id=contact_purged
    )
    cfv_active_contact = await _seed_custom_field_value(
        session, def_id=contact_def, entity_type="contact", entity_id=contact_active
    )
    await session.commit()

    # s3_client=None: skip the real S3 delete, still clear the DB reference.
    summary = await RetentionPolicyService(session, s3_client=None).apply(NOW)

    assert summary["call_phi_purged"] == 2          # call_expired + call_old
    assert summary["recordings_deleted"] == 1       # call_expired
    assert summary["contacts_anonymized"] == 1      # contact_purged
    assert summary["call_custom_fields_deleted"] == 1
    assert summary["contact_custom_fields_deleted"] == 1

    # ── expired call: PHI purged, row + contact link survive ──────────────
    row = (
        await session.execute(
            text(
                "SELECT summary_encrypted, transcript_with_tool_calls_encrypted, "
                "recording_url, recording_deleted_at, contact_id, purged_at "
                "FROM calls WHERE id = :id"
            ),
            {"id": call_expired},
        )
    ).one()
    assert row.summary_encrypted is None
    assert row.transcript_with_tool_calls_encrypted is None
    assert row.recording_url is None              # #3: cleared via record clock
    assert row.recording_deleted_at is not None
    assert str(row.contact_id) == contact_purged  # link kept for analytics
    assert row.purged_at is not None

    # ── legal hold blocks the purge ───────────────────────────────────────
    assert (
        await _scalar(session, "SELECT purged_at FROM calls WHERE id = :id", id=call_held)
        is None
    )
    assert (
        await _scalar(
            session,
            "SELECT summary_encrypted FROM calls WHERE id = :id",
            id=call_held,
        )
        == "enc-summary"
    )

    # ── recent call untouched ─────────────────────────────────────────────
    assert (
        await _scalar(
            session, "SELECT purged_at FROM calls WHERE id = :id", id=call_recent
        )
        is None
    )

    # ── contact anonymization ─────────────────────────────────────────────
    purged_contact = (
        await session.execute(
            text(
                "SELECT first_name, email_encrypted, phone_hash, "
                "nexhealth_patient_id, anonymized_at "
                "FROM contacts WHERE id = :id"
            ),
            {"id": contact_purged},
        )
    ).one()
    assert purged_contact.first_name is None
    assert purged_contact.email_encrypted is None
    assert purged_contact.phone_hash is None
    assert purged_contact.nexhealth_patient_id == "pms-purged"   # linkage kept
    assert purged_contact.anonymized_at is not None

    # contact with a recent retained call is NOT anonymized
    active_contact = (
        await session.execute(
            text(
                "SELECT first_name, anonymized_at FROM contacts WHERE id = :id"
            ),
            {"id": contact_active},
        )
    ).one()
    assert active_contact.first_name == "Jane"
    assert active_contact.anonymized_at is None

    # contact whose only call is under legal hold is NOT anonymized
    held_contact = (
        await session.execute(
            text("SELECT first_name, anonymized_at FROM contacts WHERE id = :id"),
            {"id": contact_held},
        )
    ).one()
    assert held_contact.first_name == "Jane"
    assert held_contact.anonymized_at is None

    # ── custom field values: purged with their parent, survivors kept ─────
    surviving = {
        str(r[0])
        for r in (
            await session.execute(
                text("SELECT id FROM custom_field_values WHERE institution_id = :i"),
                {"i": INSTITUTION_ID},
            )
        ).all()
    }
    assert cfv_expired_call not in surviving      # parent call purged
    assert cfv_purged_contact not in surviving    # parent contact anonymized
    assert cfv_recent_call in surviving           # parent call retained
    assert cfv_active_contact in surviving        # parent contact retained


@pytest.mark.asyncio
async def test_retention_policy_apply_is_idempotent(session: AsyncSession) -> None:
    """A second run finds nothing new to purge — the job is safe to re-run."""
    await _seed_institution(session)
    contact_id = str(uuid4())
    await _seed_contact(session, contact_id, "pms-idem")
    await _seed_call(
        session,
        call_id=str(uuid4()),
        contact_id=contact_id,
        retain_until=PAST,
        recording_retain_until=None,
    )
    await session.commit()

    first = await RetentionPolicyService(session, s3_client=None).apply(NOW)
    assert first["call_phi_purged"] == 1
    assert first["contacts_anonymized"] == 1

    second = await RetentionPolicyService(session, s3_client=None).apply(NOW)
    assert second["call_phi_purged"] == 0
    assert second["contacts_anonymized"] == 0
