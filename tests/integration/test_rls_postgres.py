from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.rls

ROOT = Path(__file__).resolve().parents[2]

INST_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
INST_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LOC_A1 = "11111111-1111-1111-1111-111111111111"
LOC_A2 = "22222222-2222-2222-2222-222222222222"
LOC_B1 = "33333333-3333-3333-3333-333333333333"
USER_ADMIN_A = "44444444-4444-4444-4444-444444444444"
USER_STAFF_A1 = "55555555-5555-5555-5555-555555555555"
USER_STAFF_A2 = "66666666-6666-6666-6666-666666666666"
USER_SUPER = "77777777-7777-7777-7777-777777777777"
CONTACT_A1 = "88888888-8888-8888-8888-888888888888"
CONTACT_A2 = "99999999-9999-9999-9999-999999999999"
CONTACT_B1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CALL_A1 = "10000000-0000-0000-0000-000000000001"
CALL_A2 = "10000000-0000-0000-0000-000000000002"
CALL_B1 = "10000000-0000-0000-0000-000000000003"
SMS_A1 = "20000000-0000-0000-0000-000000000001"
SMS_A2 = "20000000-0000-0000-0000-000000000002"


@pytest.fixture(scope="module")
def rls_database_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    PostgresContainer = postgres_module.PostgresContainer

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - depends on local Docker
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")

    try:
        raw_url = container.get_connection_url()
        yield _asyncpg_url(raw_url)
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="module")
async def rls_engine(rls_database_url: str):
    await _apply_rls_migration(rls_database_url)
    await _create_app_role(rls_database_url)

    app_database_url = _database_url_with_credentials(
        rls_database_url,
        username="rls_app",
        password="rls_app",
    )
    engine = create_async_engine(app_database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await _set_context(conn, role="SUPER_ADMIN", user_id=USER_SUPER)
        await _seed(conn)
    try:
        yield engine
    finally:
        await engine.dispose()


def _asyncpg_url(raw_url: str) -> str:
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql+psycopg2://"):
        return raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_url


def _database_url_with_credentials(
    database_url: str,
    *,
    username: str,
    password: str,
) -> str:
    return make_url(database_url).set(
        username=username,
        password=password,
    ).render_as_string(hide_password=False)


async def _apply_rls_migration(database_url: str) -> None:
    """Apply the FULL Alembic chain to head against the fresh Postgres schema.

    Runs the real ``alembic upgrade head`` so the test DB carries every policy
    production does — not a hand-picked baseline. Previously this loaded only
    the May consolidated baseline, so the suite silently stopped covering every
    policy added after it (call_metrics_daily, the MFA auth-lookup contexts, the
    DSO/group policies). Walking to head keeps the RLS suite honest as
    migrations land, and also proves the chain applies cleanly on a fresh DB.

    env.py's online runner calls ``asyncio.run`` internally, so the synchronous
    ``command.upgrade`` is dispatched to a worker thread rather than nested in
    this fixture's event loop.
    """
    await asyncio.to_thread(_upgrade_to_head, database_url)


async def _create_app_role(database_url: str) -> None:
    """Create a non-superuser role so PostgreSQL actually enforces RLS."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE ROLE rls_app LOGIN PASSWORD 'rls_app'"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO rls_app"))
            await conn.execute(
                text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rls_app")
            )
            await conn.execute(
                text("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO rls_app")
            )
            await conn.execute(text("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rls_app"))
    finally:
        await engine.dispose()


def _upgrade_to_head(database_url: str) -> None:
    """Run ``alembic upgrade head`` against the given DB (sync, off-loop).

    Walking the real chain to head (rather than hand-picking migration files)
    means the test DB always carries every policy production does, the suite
    self-maintains as migrations land, and a migration that fails to apply on a
    fresh DB fails the suite here instead of silently in prod.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


async def _set_context(
    conn,
    *,
    context_type: str = "user",
    user_id: str = "",
    role: str = "",
    institution_id: str = "",
    location_id: str = "",
    external_id: str = "",
    group_id: str = "",
) -> None:
    values = {
        "app.context_type": context_type,
        "app.user_id": user_id,
        "app.role": role,
        "app.institution_id": institution_id,
        "app.location_id": location_id,
        "app.external_id": external_id,
        "app.group_id": group_id,
    }
    for key, value in values.items():
        await conn.execute(
            text("SELECT set_config(:key, :value, false)"),
            {"key": key, "value": value},
        )


async def _clear_context(conn) -> None:
    await _set_context(conn)


async def _seed(conn) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO institutions (id, name, slug, is_active)
            VALUES
              (:inst_a, 'Clinic A', 'clinic-a', true),
              (:inst_b, 'Clinic B', 'clinic-b', true)
            """
        ),
        {"inst_a": INST_A, "inst_b": INST_B},
    )
    await conn.execute(
        text(
            """
            INSERT INTO institution_locations
              (id, institution_id, name, slug, is_active, retell_agent_id,
               twilio_from_number, timezone)
            VALUES
              (:loc_a1, :inst_a, 'Clinic A One', 'a-one', true, 'agent-a1',
               '+15550000001', 'UTC'),
              (:loc_a2, :inst_a, 'Clinic A Two', 'a-two', true, 'agent-a2',
               '+15550000002', 'UTC'),
              (:loc_b1, :inst_b, 'Clinic B One', 'b-one', true, 'agent-b1',
               '+15550000003', 'UTC')
            """
        ),
        {
            "loc_a1": LOC_A1,
            "loc_a2": LOC_A2,
            "loc_b1": LOC_B1,
            "inst_a": INST_A,
            "inst_b": INST_B,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO users
              (id, email, role, institution_id, location_id, invite_status,
               is_active, created_at)
            VALUES
              (:admin_a, 'admin-a@example.com', 'INSTITUTION_ADMIN', :inst_a,
               NULL, 'ACCEPTED', true, now()),
              (:staff_a1, 'staff-a1@example.com', 'STAFF', :inst_a,
               :loc_a1, 'ACCEPTED', true, now()),
              (:staff_a2, 'staff-a2@example.com', 'STAFF', :inst_a,
               :loc_a2, 'ACCEPTED', true, now()),
              (:super, 'super@example.com', 'SUPER_ADMIN', NULL, NULL,
               'ACCEPTED', true, now())
            """
        ),
        {
            "admin_a": USER_ADMIN_A,
            "staff_a1": USER_STAFF_A1,
            "staff_a2": USER_STAFF_A2,
            "super": USER_SUPER,
            "inst_a": INST_A,
            "loc_a1": LOC_A1,
            "loc_a2": LOC_A2,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO contacts (id, institution_id, full_name, is_new_patient)
            VALUES
              (:contact_a1, :inst_a, 'Patient A1', false),
              (:contact_a2, :inst_a, 'Patient A2', false),
              (:contact_b1, :inst_b, 'Patient B1', false)
            """
        ),
        {
            "contact_a1": CONTACT_A1,
            "contact_a2": CONTACT_A2,
            "contact_b1": CONTACT_B1,
            "inst_a": INST_A,
            "inst_b": INST_B,
        },
    )
    for contact_id, location_id in ((CONTACT_A1, LOC_A1), (CONTACT_A2, LOC_A2)):
        await conn.execute(
            text(
                """
                INSERT INTO contact_location_accesses
                  (id, institution_id, contact_id, location_id)
                VALUES (:id, :inst_a, :contact_id, :location_id)
                """
            ),
            {
                "id": str(UUID(bytes=UUID(contact_id).bytes[:8] + UUID(location_id).bytes[:8])),
                "inst_a": INST_A,
                "contact_id": contact_id,
                "location_id": location_id,
            },
        )
    await conn.execute(
        text(
            """
            INSERT INTO calls
              (id, institution_id, contact_id, location_id, retell_call_id,
               is_new_patient, is_complaint, is_insurance_billing,
               callback_resolved, times_called)
            VALUES
              (:call_a1, :inst_a, :contact_a1, :loc_a1, 'call-a1', false, false, false, false, 1),
              (:call_a2, :inst_a, :contact_a2, :loc_a2, 'call-a2', false, false, false, false, 1),
              (:call_b1, :inst_b, :contact_b1, :loc_b1, 'call-b1', false, false, false, false, 1)
            """
        ),
        {
            "call_a1": CALL_A1,
            "call_a2": CALL_A2,
            "call_b1": CALL_B1,
            "inst_a": INST_A,
            "inst_b": INST_B,
            "contact_a1": CONTACT_A1,
            "contact_a2": CONTACT_A2,
            "contact_b1": CONTACT_B1,
            "loc_a1": LOC_A1,
            "loc_a2": LOC_A2,
            "loc_b1": LOC_B1,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO sms_history_logs
              (id, from_number, to_number_encrypted, body_encrypted,
               to_number_hash, to_number_masked, status, message_sid,
               institution_id, location_id, institution_location_id, timestamp)
            VALUES
              (:sms_a1, '+15550000001', 'cipher', 'cipher', 'hash-a1', '***0001',
               'sent', 'SM_A1', :inst_a, :loc_a1, :loc_a1, now()),
              (:sms_a2, '+15550000002', 'cipher', 'cipher', 'hash-a2', '***0002',
               'sent', 'SM_A2', :inst_a, :loc_a2, :loc_a2, now())
            """
        ),
        {
            "sms_a1": SMS_A1,
            "sms_a2": SMS_A2,
            "inst_a": INST_A,
            "loc_a1": LOC_A1,
            "loc_a2": LOC_A2,
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO notifications
              (id, institution_id, user_id, type, title_encrypted, message_encrypted, is_read)
            VALUES
              ('30000000-0000-0000-0000-000000000001', :inst_a, :staff_a1,
               'new_call', 'cipher', 'cipher', false),
              ('30000000-0000-0000-0000-000000000002', :inst_a, :staff_a2,
               'new_call', 'cipher', 'cipher', false)
            """
        ),
        {"inst_a": INST_A, "staff_a1": USER_STAFF_A1, "staff_a2": USER_STAFF_A2},
    )


@pytest.mark.asyncio
async def test_rls_blocks_no_context_and_enforces_user_scope(rls_engine) -> None:
    async with rls_engine.begin() as conn:
        await _clear_context(conn)
        assert await conn.scalar(text("SELECT count(*) FROM calls")) == 0
        with pytest.raises(DBAPIError):
            await conn.execute(
                text(
                    """
                    INSERT INTO calls
                      (id, institution_id, retell_call_id, is_new_patient,
                       is_complaint, is_insurance_billing, callback_resolved, times_called)
                    VALUES
                      ('90000000-0000-0000-0000-000000000001', :inst_a,
                       'blocked-call', false, false, false, false, 1)
                    """
                ),
                {"inst_a": INST_A},
            )

    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            user_id=USER_ADMIN_A,
            role="INSTITUTION_ADMIN",
            institution_id=INST_A,
        )
        assert await conn.scalar(text("SELECT count(*) FROM calls")) == 2
        assert await conn.scalar(text("SELECT count(*) FROM contacts")) == 2

    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            user_id=USER_STAFF_A1,
            role="STAFF",
            institution_id=INST_A,
            location_id=LOC_A1,
        )
        assert await conn.scalar(text("SELECT count(*) FROM calls")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM contacts")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM sms_history_logs")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM notifications")) == 1

    async with rls_engine.begin() as conn:
        await _set_context(conn, user_id=USER_SUPER, role="SUPER_ADMIN")
        assert await conn.scalar(text("SELECT count(*) FROM calls")) == 3


@pytest.mark.asyncio
async def test_rls_system_contexts_are_narrow(rls_engine) -> None:
    async with rls_engine.begin() as conn:
        await _set_context(conn, context_type="twilio_status", external_id="SM_A1")
        assert await conn.scalar(text("SELECT count(*) FROM sms_history_logs")) == 1
        await conn.execute(
            text("UPDATE sms_history_logs SET provider_status = 'delivered'")
        )

    async with rls_engine.begin() as conn:
        await _set_context(conn, context_type="twilio_status", external_id="SM_A2")
        assert await conn.scalar(
            text(
                """
                SELECT count(*) FROM sms_history_logs
                WHERE provider_status = 'delivered'
                """
            )
        ) == 0

    async with rls_engine.begin() as conn:
        await _set_context(conn, context_type="retell", external_id="retell-new-call")
        await conn.execute(
            text(
                """
                INSERT INTO retell_webhook_events
                  (id, call_id, event_type, status, attempts, created_at, updated_at)
                VALUES
                  ('40000000-0000-0000-0000-000000000001',
                   'retell-new-call', 'call_analyzed', 'PROCESSING', 1, now(), now())
                """
            )
        )
        assert await conn.scalar(text("SELECT count(*) FROM retell_webhook_events")) == 1

    async with rls_engine.begin() as conn:
        await _set_context(conn, context_type="retell", external_id="different-call")
        assert await conn.scalar(text("SELECT count(*) FROM retell_webhook_events")) == 0


@pytest.mark.asyncio
async def test_rls_login_flow(rls_engine) -> None:
    """Auth email context allows email lookup; cleared context blocks all.

    Mirrors auth.py:_auth_db_session("auth_email", external_id=email), where
    login by email runs before the JWT-validated user_id is known.
    """
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            context_type="auth_email",
            external_id="admin-a@example.com",
        )
        result = await conn.scalar(
            text("SELECT count(*) FROM users WHERE email = :email"),
            {"email": "admin-a@example.com"},
        )
        assert result == 1

    async with rls_engine.begin() as conn:
        await _clear_context(conn)
        result = await conn.scalar(
            text("SELECT count(*) FROM users WHERE email = :email"),
            {"email": "admin-a@example.com"},
        )
        assert result == 0


@pytest.mark.asyncio
async def test_rls_institution_owned_tables_isolate_system_contexts(
    rls_engine,
) -> None:
    """System contexts must scope custom_field_definitions, email_templates,
    and external_notification_recipients by institution_id.

    Regression guard for H-RLS-1 — these tables previously had a permissive
    policy that exposed all rows to any system context.
    """
    cfd_a = "a1000000-0000-0000-0000-000000000001"
    cfd_b = "a1000000-0000-0000-0000-000000000002"
    et_a = "a2000000-0000-0000-0000-000000000001"
    et_b = "a2000000-0000-0000-0000-000000000002"
    enr_a = "a3000000-0000-0000-0000-000000000001"
    enr_b = "a3000000-0000-0000-0000-000000000002"
    ws_a = "a5000000-0000-0000-0000-000000000001"
    ws_b = "a5000000-0000-0000-0000-000000000002"

    # Seed under SUPER_ADMIN context (matches existing module-load pattern)
    async with rls_engine.begin() as conn:
        await _set_context(conn, role="SUPER_ADMIN", user_id=USER_SUPER)
        await conn.execute(
            text(
                """
                INSERT INTO custom_field_definitions
                  (id, institution_id, entity_type, field_name, field_key,
                   field_type, is_phi, is_required, display_order, is_active)
                VALUES
                  (:cfd_a, :inst_a, 'contact', 'Referral A', 'referral_a',
                   'text', false, false, 0, true),
                  (:cfd_b, :inst_b, 'contact', 'Referral B', 'referral_b',
                   'text', false, false, 0, true)
                """
            ),
            {"cfd_a": cfd_a, "cfd_b": cfd_b, "inst_a": INST_A, "inst_b": INST_B},
        )
        await conn.execute(
            text(
                """
                INSERT INTO email_templates
                  (id, institution_id, template_type, name, subject_template,
                   html_body, text_body, is_active)
                VALUES
                  (:et_a, :inst_a, 'call_summary', 'Tpl A', 'Subj A',
                   '<p>A</p>', 'A', true),
                  (:et_b, :inst_b, 'call_summary', 'Tpl B', 'Subj B',
                   '<p>B</p>', 'B', true)
                """
            ),
            {"et_a": et_a, "et_b": et_b, "inst_a": INST_A, "inst_b": INST_B},
        )
        await conn.execute(
            text(
                """
                INSERT INTO external_notification_recipients
                  (id, institution_id, email, template_type, is_active)
                VALUES
                  (:enr_a, :inst_a, 'a@example.com', 'call_summary', true),
                  (:enr_b, :inst_b, 'b@example.com', 'call_summary', true)
                """
            ),
            {"enr_a": enr_a, "enr_b": enr_b, "inst_a": INST_A, "inst_b": INST_B},
        )
        await conn.execute(
            text(
                """
                INSERT INTO workflow_statuses
                  (id, institution_id, name, color, display_order, is_active)
                VALUES
                  (:ws_a, :inst_a, 'Done A', 'emerald', 0, true),
                  (:ws_b, :inst_b, 'Done B', 'rose', 0, true)
                """
            ),
            {"ws_a": ws_a, "ws_b": ws_b, "inst_a": INST_A, "inst_b": INST_B},
        )

    # For each system context with institution A, must see only A's rows
    for context_type in ("celery", "twilio", "retell", "dead_letter"):
        async with rls_engine.begin() as conn:
            await _set_context(
                conn,
                context_type=context_type,
                institution_id=INST_A,
            )
            cfd_count = await conn.scalar(
                text("SELECT count(*) FROM custom_field_definitions")
            )
            et_count = await conn.scalar(text("SELECT count(*) FROM email_templates"))
            enr_count = await conn.scalar(
                text("SELECT count(*) FROM external_notification_recipients")
            )
            ws_count = await conn.scalar(text("SELECT count(*) FROM workflow_statuses"))
            assert ws_count == 1, (
                f"{context_type}: workflow_statuses visible={ws_count}, "
                f"expected 1 (only INST_A row)"
            )
            assert cfd_count == 1, (
                f"{context_type}: custom_field_definitions visible={cfd_count}, "
                f"expected 1 (only INST_A row)"
            )
            assert et_count == 1, (
                f"{context_type}: email_templates visible={et_count}, "
                f"expected 1 (only INST_A row)"
            )
            assert enr_count == 1, (
                f"{context_type}: external_notification_recipients "
                f"visible={enr_count}, expected 1 (only INST_A row)"
            )


@pytest.mark.asyncio
async def test_rls_audit_context_can_stamp_own_institution_jurisdiction(
    rls_engine,
) -> None:
    """Audit context must read its scoped institution for jurisdiction stamping."""
    audit_id = "a4000000-0000-0000-0000-000000000001"

    async with rls_engine.begin() as conn:
        await _set_context(conn, role="SUPER_ADMIN", user_id=USER_SUPER)
        await conn.execute(
            text("UPDATE institutions SET jurisdiction = 'CA-AB' WHERE id = :inst_a"),
            {"inst_a": INST_A},
        )

    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            context_type="audit",
            institution_id=INST_A,
            location_id=LOC_A1,
            user_id=USER_STAFF_A1,
        )
        assert (
            await conn.scalar(
                text("SELECT jurisdiction FROM institutions WHERE id = :inst_a"),
                {"inst_a": INST_A},
            )
        ) == "CA-AB"
        assert (
            await conn.scalar(
                text("SELECT jurisdiction FROM institutions WHERE id = :inst_b"),
                {"inst_b": INST_B},
            )
        ) is None
        await conn.execute(
            text(
                """
                INSERT INTO audit_logs
                  (id, timestamp, actor, action, target_resource, outcome,
                   audit_metadata, institution_id, location_id, user_id)
                VALUES
                  (:audit_id, now(), 'ADMIN', 'VIEW_CALL_DETAIL',
                   'audit-jurisdiction-proof', 'SUCCESS',
                   jsonb_build_object(
                     'jurisdiction',
                     (SELECT jurisdiction FROM institutions WHERE id = :inst_a)
                   ),
                   :inst_a, :loc_a1, :staff_a1)
                """
            ),
            {
                "audit_id": audit_id,
                "inst_a": INST_A,
                "loc_a1": LOC_A1,
                "staff_a1": USER_STAFF_A1,
            },
        )
        assert (
            await conn.scalar(
                text(
                    """
                    SELECT audit_metadata->>'jurisdiction'
                    FROM audit_logs
                    WHERE id = :audit_id
                    """
                ),
                {"audit_id": audit_id},
            )
        ) == "CA-AB"


@pytest.mark.asyncio
async def test_rls_institution_locations_branches(rls_engine) -> None:
    """Cover each branch of _institution_locations_expr."""
    # middleware_lookup: external_id=institution_slug -> sees that inst's locations
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            context_type="middleware_lookup",
            external_id="clinic-a",
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM institution_locations"))
        ) == 2

    # retell_lookup: external_id=retell_agent_id -> sees that one location only
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            context_type="retell_lookup",
            external_id="agent-a1",
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM institution_locations"))
        ) == 1

    # twilio_lookup: external_id=twilio_from_number -> sees that one location only
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            context_type="twilio_lookup",
            external_id="+15550000001",
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM institution_locations"))
        ) == 1

    # user + INSTITUTION_ADMIN: sees ALL institution locations (no location_id)
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            user_id=USER_ADMIN_A,
            role="INSTITUTION_ADMIN",
            institution_id=INST_A,
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM institution_locations"))
        ) == 2

    # user + LOCATION_ADMIN: sees only their location
    async with rls_engine.begin() as conn:
        await _set_context(
            conn,
            user_id=USER_STAFF_A1,
            role="LOCATION_ADMIN",
            institution_id=INST_A,
            location_id=LOC_A1,
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM institution_locations"))
        ) == 1


# ── Institution-group (DSO oversight) isolation ─────────────────────────────────

GRP_1 = "c1111111-1111-1111-1111-111111111111"
GRP_2 = "c2222222-2222-2222-2222-222222222222"
INST_G1 = "d1111111-1111-1111-1111-111111111111"
INST_G2 = "d2222222-2222-2222-2222-222222222222"
LOC_G1 = "e1111111-1111-1111-1111-111111111111"
USER_GA1 = "f1111111-1111-1111-1111-111111111111"
USER_GA2 = "f2222222-2222-2222-2222-222222222222"
SENTINEL = "00000000-0000-0000-0000-000000000000"


async def _seed_groups(conn) -> None:
    await conn.execute(
        text(
            "INSERT INTO institution_groups (id, name, slug, is_active) VALUES "
            "(:g1,'Group One','group-one',true),(:g2,'Group Two','group-two',true)"
        ),
        {"g1": GRP_1, "g2": GRP_2},
    )
    await conn.execute(
        text(
            "INSERT INTO institutions (id, name, slug, is_active, group_id) VALUES "
            "(:i1,'G1 Clinic','g1-clinic',true,:g1),(:i2,'G2 Clinic','g2-clinic',true,:g2)"
        ),
        {"i1": INST_G1, "i2": INST_G2, "g1": GRP_1, "g2": GRP_2},
    )
    await conn.execute(
        text(
            "INSERT INTO institution_locations (id, institution_id, name, slug, is_active, timezone) "
            "VALUES (:l1,:i1,'G1 Main','g1-main',true,'UTC')"
        ),
        {"l1": LOC_G1, "i1": INST_G1},
    )
    await conn.execute(
        text(
            "INSERT INTO call_metrics_daily "
            "(institution_id, location_id, call_date, total_calls, new_patient_calls, "
            " complaint_calls, insurance_billing_calls, total_duration_seconds, tag_counts, updated_at) VALUES "
            "(:i1,:l1, current_date, 10, 0, 0, 0, 0, '{}'::jsonb, now()),"
            "(:i2,:s, current_date, 20, 0, 0, 0, 0, '{}'::jsonb, now())"
        ),
        {"i1": INST_G1, "l1": LOC_G1, "i2": INST_G2, "s": SENTINEL},
    )
    await conn.execute(
        text(
            "INSERT INTO users (id, email, role, institution_id, location_id, group_id, "
            "invite_status, is_active, created_at) VALUES "
            "(:ga1,'ga1@example.com','GROUP_ADMIN',NULL,NULL,:g1,'ACCEPTED',true,now()),"
            "(:ga2,'ga2@example.com','GROUP_ADMIN',NULL,NULL,:g2,'ACCEPTED',true,now())"
        ),
        {"ga1": USER_GA1, "ga2": USER_GA2, "g1": GRP_1, "g2": GRP_2},
    )


async def _teardown_groups(conn) -> None:
    await conn.execute(text("DELETE FROM call_metrics_daily WHERE institution_id = ANY(:ids)"),
                       {"ids": [INST_G1, INST_G2]})
    await conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [USER_GA1, USER_GA2]})
    await conn.execute(text("DELETE FROM institution_locations WHERE id = :l"), {"l": LOC_G1})
    await conn.execute(text("DELETE FROM institutions WHERE id = ANY(:ids)"), {"ids": [INST_G1, INST_G2]})
    await conn.execute(text("DELETE FROM institution_groups WHERE id = ANY(:ids)"), {"ids": [GRP_1, GRP_2]})


@pytest.mark.asyncio
async def test_rls_group_admin_isolation(rls_engine) -> None:
    """A GROUP_ADMIN reads only its own group; cross-group is ∅ and writes are rejected."""
    # Setup as SUPER_ADMIN (bypasses RLS via WITH CHECK).
    async with rls_engine.begin() as conn:
        await _set_context(conn, role="SUPER_ADMIN", user_id=USER_SUPER)
        await _seed_groups(conn)

    try:
        # ── As GROUP_ADMIN of group 1 (no institution scope) ──────────────
        async with rls_engine.begin() as conn:
            await _set_context(conn, role="GROUP_ADMIN", user_id=USER_GA1, group_id=GRP_1)

            # Own group's institution visible; the other group's is NOT.
            assert await conn.scalar(
                text("SELECT count(*) FROM institutions WHERE id = :i"), {"i": INST_G1}
            ) == 1
            assert await conn.scalar(
                text("SELECT count(*) FROM institutions WHERE id = :i"), {"i": INST_G2}
            ) == 0

            # Own group's rollup visible; the other group's rollup is ∅.
            assert await conn.scalar(
                text("SELECT count(*) FROM call_metrics_daily WHERE institution_id = :i"),
                {"i": INST_G1},
            ) == 1
            assert await conn.scalar(
                text("SELECT count(*) FROM call_metrics_daily WHERE institution_id = :i"),
                {"i": INST_G2},
            ) == 0
            # Group-wide read sees only its own member's rollup.
            assert await conn.scalar(text("SELECT count(*) FROM call_metrics_daily")) == 1

        # Note: the per-request app.institution_id GUC is trusted by RLS (the
        # generic user scope clause keys on it for every role), so cross-group
        # protection on drill-in lives at the app layer — the /group drill-in
        # endpoint verifies membership BEFORE setting institution_id. The RLS
        # guarantee tested here is the aggregate (no-institution) context above,
        # which isolates by group via the membership clause.

        # ── Legit drill-in: GROUP_ADMIN of G1 scoped to its own member ────
        async with rls_engine.begin() as conn:
            await _set_context(
                conn, role="GROUP_ADMIN", user_id=USER_GA1,
                group_id=GRP_1, institution_id=INST_G1,
            )
            assert await conn.scalar(
                text("SELECT count(*) FROM institution_locations WHERE institution_id = :i"),
                {"i": INST_G1},
            ) == 1

        # ── Writes are rejected for a GROUP_ADMIN (read-only oversight) ───
        async with rls_engine.begin() as conn:
            await _set_context(conn, role="GROUP_ADMIN", user_id=USER_GA1, group_id=GRP_1)
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text("UPDATE call_metrics_daily SET total_calls = 999 WHERE institution_id = :i"),
                    {"i": INST_G1},
                )
        async with rls_engine.begin() as conn:
            await _set_context(conn, role="GROUP_ADMIN", user_id=USER_GA1, group_id=GRP_1)
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO institution_groups (id, name, slug, is_active) "
                        "VALUES ('c9999999-9999-9999-9999-999999999999','x','x',true)"
                    )
                )
    finally:
        async with rls_engine.begin() as conn:
            await _set_context(conn, role="SUPER_ADMIN", user_id=USER_SUPER)
            await _teardown_groups(conn)
