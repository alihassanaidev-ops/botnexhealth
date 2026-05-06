from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pyotp
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.database import (
    RlsContext,
    close_database,
    get_db_session,
    get_system_db_session,
    init_database,
    use_rls_context,
)
from src.app.main import app
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.mfa import MfaRecoveryCode
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import RefreshTokenService


pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


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


async def _create_app_role(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE ROLE mfa_rls_app LOGIN PASSWORD 'mfa_rls_app'"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO mfa_rls_app"))
            await conn.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE "
                    "ON ALL TABLES IN SCHEMA public TO mfa_rls_app"
                )
            )
            await conn.execute(
                text(
                    "GRANT USAGE, SELECT, UPDATE "
                    "ON ALL SEQUENCES IN SCHEMA public TO mfa_rls_app"
                )
            )
            await conn.execute(text("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO mfa_rls_app"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def postgres_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    PostgresContainer = postgres_module.PostgresContainer
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - local Docker dependent
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")
    try:
        yield _asyncpg_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture(scope="module")
def redis_url() -> str:
    redis_module = pytest.importorskip("testcontainers.redis")
    RedisContainer = redis_module.RedisContainer
    try:
        container = RedisContainer("redis:7-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - local Docker dependent
        pytest.skip(f"Redis Testcontainer unavailable: {exc}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture(scope="module")
def migrated_postgres(postgres_url: str) -> str:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(cfg, "head")
    asyncio.run(_create_app_role(postgres_url))
    return _database_url_with_credentials(
        postgres_url,
        username="mfa_rls_app",
        password="mfa_rls_app",
    )


@pytest.fixture(autouse=True)
def _reset_rate_limiter_between_real_stack_tests():
    """All real-stack tests hit the app from 127.0.0.1, so the
    ``RATE_AUTH = "10/minute"`` limiter on /api/auth/login eventually
    starts returning 429 across multiple tests. Reset between tests."""
    from src.app.api.rate_limit import limiter
    limiter.reset()
    yield
    limiter.reset()


@pytest_asyncio.fixture
async def real_stack(monkeypatch: pytest.MonkeyPatch, migrated_postgres: str, redis_url: str):
    monkeypatch.setattr(settings, "database_url", migrated_postgres)
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "celery_broker_url", None)
    monkeypatch.setattr(settings, "encryption_key", "test-mfa-encryption-key-material-32")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "webauthn_rp_id", "localhost")
    monkeypatch.setattr(settings, "webauthn_allowed_origins", "http://test")
    RefreshTokenService._client = None
    init_database(migrated_postgres)
    try:
        yield
    finally:
        if RefreshTokenService._client is not None:
            await RefreshTokenService._client.aclose()
            RefreshTokenService._client = None
        await close_database()


async def _seed_user(email: str, *, role: str, location_id: str | None = None) -> tuple[str, str, str]:
    institution_id = str(uuid4())
    location = location_id or str(uuid4())
    user_id = str(uuid4())
    async with get_system_db_session(
        "user",
        role=UserRole.SUPER_ADMIN.value,
        user_id="00000000-0000-0000-0000-000000000000",
    ) as session:
        institution = Institution(
            id=institution_id,
            name=f"Clinic {email}",
            slug=f"clinic-{user_id[:8]}",
            is_active=True,
        )
        session.add(institution)
        session.add(
            InstitutionLocation(
                id=location,
                institution_id=institution_id,
                name="Main",
                slug=f"loc-{user_id[:8]}",
                is_active=True,
            )
        )
        session.add(
            User(
                id=user_id,
                email=email,
                role=role,
                institution_id=institution_id,
                location_id=location if role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value) else None,
                is_active=True,
                invite_status=InviteStatus.ACCEPTED.value,
                password_hash=PasswordService.hash_password("ValidPass123!"),
            )
        )
    return user_id, institution_id, location


@pytest.mark.asyncio
async def test_login_totp_setup_users_me_and_refresh_with_real_postgres_redis(real_stack):
    user_id, _, _ = await _seed_user("mfa-real@example.com", role=UserRole.INSTITUTION_ADMIN.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "mfa-real@example.com", "password": "ValidPass123!"},
        )
        assert login.status_code == 200
        login_body = login.json()
        assert login_body["status"] == "mfa_setup_required"
        assert "totp" in login_body["setup_methods"]

        setup = await client.post(
            "/api/auth/mfa/totp/setup/options",
            json={"mfa_ticket": login_body["mfa_ticket"]},
        )
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        code = pyotp.TOTP(secret).now()

        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": login_body["mfa_ticket"], "code": code},
        )
        assert verify.status_code == 200
        session = verify.json()
        assert session["status"] == "authenticated"
        assert session["access_token"]
        assert len(session["recovery_codes"]) == 10
        assert client.cookies.get(settings.refresh_cookie_name)

        me = await client.get(
            "/api/auth/users/me",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] == user_id

        refresh = await client.post("/api/auth/refresh")
        assert refresh.status_code == 200
        assert refresh.json()["status"] == "authenticated"


@pytest.mark.asyncio
async def test_wrong_totp_writes_failure_audit_row_in_real_postgres(real_stack):
    """End-to-end: wrong TOTP code returns 401 AND persists a
    MFA_VERIFY/FAILURE_UNAUTHORIZED row in the real audit_logs table.

    Pins the §164.312(b) contract end-to-end: the failed verify
    survives the audit pipeline and is queryable by activity reviewers.
    """
    from src.app.models.audit_log import AuditAction, AuditLog, AuditOutcome
    from src.app.services.audit import AuditService, PostgresAuditRepository, set_audit_service

    # The conftest installs an in-memory audit repo for every test
    # (autouse=True). For this test we need the real PostgresAuditRepository
    # so the row actually lands in audit_logs and we can query it back.
    set_audit_service(AuditService(PostgresAuditRepository()))

    user_id, _, _ = await _seed_user(
        "mfa-fail-audit@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "mfa-fail-audit@example.com", "password": "ValidPass123!"},
        )
        assert login.status_code == 200
        ticket = login.json()["mfa_ticket"]

        setup = await client.post(
            "/api/auth/mfa/totp/setup/options",
            json={"mfa_ticket": ticket},
        )
        secret = setup.json()["secret"]
        code = pyotp.TOTP(secret).now()
        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": code},
        )
        assert verify.status_code == 200, verify.text
        client.cookies.clear()

        # New session: log in again (fresh ticket) and submit a wrong code.
        relogin = await client.post(
            "/api/auth/login",
            json={"email": "mfa-fail-audit@example.com", "password": "ValidPass123!"},
        )
        assert relogin.status_code == 200, relogin.text
        attacker_ticket = relogin.json()["mfa_ticket"]
        wrong = await client.post(
            "/api/auth/mfa/totp/verify",
            json={"mfa_ticket": attacker_ticket, "code": "000000"},
        )
        assert wrong.status_code == 401, wrong.text

    # Drain background audit writes, then query the real audit_logs table.
    from src.app.services.audit import AuditService

    await AuditService.drain_background_tasks()

    async with get_system_db_session("audit") as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.MFA_VERIFY.value,
                AuditLog.outcome == AuditOutcome.FAILURE_UNAUTHORIZED.value,
                AuditLog.user_id == user_id,
            )
        )
        rows = list(result.scalars().all())

    assert len(rows) == 1, (
        f"Expected exactly one MFA_VERIFY/FAILURE_UNAUTHORIZED row in "
        f"audit_logs for the failed attempt; got {len(rows)}"
    )

    row = rows[0]
    assert row.target_resource == f"user:{user_id}"
    assert row.audit_metadata is not None
    assert row.audit_metadata.get("method") == "totp"
    assert row.audit_metadata.get("phase") == "verify"
    assert row.audit_metadata.get("error_type") == "MfaVerificationFailed"


@pytest.mark.asyncio
async def test_refresh_session_redis_ttl_matches_idle_timeout(real_stack):
    """End-to-end against real Redis: an issued refresh token's TTL is
    the configured idle-timeout window (M1, HIPAA §164.312(a)(2)(iii))."""
    from redis.asyncio import from_url

    user_id, _, _ = await _seed_user(
        "ttl-real@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    # Drive the full enroll flow so the cookie is issued.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "ttl-real@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        secret = (await client.post(
            "/api/auth/mfa/totp/setup/options", json={"mfa_ticket": ticket},
        )).json()["secret"]
        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 200, verify.text

    # Inspect Redis directly — the refresh-session key must have a TTL
    # bounded by the configured 1-hour idle window, not days.
    expected_ttl = settings.refresh_token_ttl_minutes * 60

    redis = from_url(settings.redis_url, decode_responses=True)
    try:
        keys = await redis.keys(f"refresh:{user_id}:*")
        assert keys, "Expected exactly one refresh-session key for the user"
        ttls = [await redis.ttl(k) for k in keys]
    finally:
        await redis.aclose()

    # Server has ~milliseconds of slack between SETEX and the TTL read,
    # so allow a small floor; ceiling = the configured value.
    assert ttls, "Redis returned no TTL for refresh session"
    longest = max(ttls)
    assert expected_ttl - 5 <= longest <= expected_ttl, (
        f"Refresh-session Redis TTL ({longest}s) should match the configured "
        f"idle window ({expected_ttl}s)"
    )
    # Catch the regression cleanly: must NOT exceed one day.
    assert longest < 24 * 60 * 60, (
        "Refresh-session Redis TTL is multi-day — HIPAA idle-logoff window violated"
    )


@pytest.mark.asyncio
async def test_totp_disable_round_trip_with_real_postgres(real_stack):
    """End-to-end: enroll TOTP → call /mfa/totp/disable → row gone + audit row.

    Pins the §164.312(b) contract for factor disablement against a real
    Postgres + Redis stack: the user can self-service disable a factor,
    the table reflects it, and the audit row is durable.
    """
    from src.app.models.audit_log import AuditAction, AuditLog, AuditOutcome
    from src.app.models.mfa import UserTotpFactor
    from src.app.services.audit import (
        AuditService, PostgresAuditRepository, set_audit_service,
    )

    set_audit_service(AuditService(PostgresAuditRepository()))

    user_id, _, _ = await _seed_user(
        "mfa-disable@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Login → enroll TOTP → get authenticated session.
        login = await client.post(
            "/api/auth/login",
            json={"email": "mfa-disable@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        setup = await client.post(
            "/api/auth/mfa/totp/setup/options",
            json={"mfa_ticket": ticket},
        )
        secret = setup.json()["secret"]
        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 200, verify.text
        access_token = verify.json()["access_token"]

        # Sanity: TOTP factor row exists.
        async with get_system_db_session("user", role=UserRole.SUPER_ADMIN.value, user_id=user_id) as session:
            existing = await session.execute(
                select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
            )
            assert existing.scalars().first() is not None

        # 2. Call /mfa/totp/disable.
        disable = await client.post(
            "/api/auth/mfa/totp/disable",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert disable.status_code == 200
        assert disable.json() == {"message": "Authenticator app disabled"}

        # 3. Idempotent — second call returns the no-op message.
        disable_again = await client.post(
            "/api/auth/mfa/totp/disable",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert disable_again.status_code == 200
        assert disable_again.json() == {"message": "Authenticator app was not enabled"}

    # 4. TOTP row is gone from the real Postgres table.
    async with get_system_db_session("user", role=UserRole.SUPER_ADMIN.value, user_id=user_id) as session:
        gone = await session.execute(
            select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        assert gone.scalars().first() is None

    # 5. Exactly one MFA_FACTOR_DISABLE audit row landed (idempotent retry
    # didn't write a second one).
    from src.app.services.audit import AuditService as _AuditSvc
    await _AuditSvc.drain_background_tasks()
    async with get_system_db_session("audit") as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.MFA_FACTOR_DISABLE.value,
                AuditLog.outcome == AuditOutcome.SUCCESS.value,
                AuditLog.user_id == user_id,
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 1, f"Expected exactly one audit row, got {len(rows)}"
    row = rows[0]
    assert row.target_resource == f"user:{user_id}/totp"
    assert row.audit_metadata is not None
    assert row.audit_metadata.get("method") == "totp"


@pytest.mark.asyncio
async def test_mfa_rls_blocks_cross_user_recovery_codes(real_stack):
    user_a, _, _ = await _seed_user("mfa-a@example.com", role=UserRole.INSTITUTION_ADMIN.value)
    user_b, _, _ = await _seed_user("mfa-b@example.com", role=UserRole.INSTITUTION_ADMIN.value)

    async with get_system_db_session(
        "auth",
        user_id=user_b,
    ) as session:
        session.add(
            MfaRecoveryCode(
                user_id=user_b,
                code_hash=PasswordService.hash_secret("ABCDE12345"),
            )
        )

    with use_rls_context(
        RlsContext(
            context_type="user",
            user_id=user_a,
            role=UserRole.INSTITUTION_ADMIN.value,
        )
    ):
        async with get_db_session() as session:
            result = await session.execute(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_b)
            )
            assert result.scalars().all() == []
