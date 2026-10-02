import sys
import os

# Add project root to python path before importing src
sys.path.append(os.getcwd())

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.app.config import Settings, settings
from src.app.dependencies import cleanup_nexhealth_client, init_nexhealth_client
from src.app.main import app
from src.app.nexhealth.client import NexHealthClient
from src.app.retell.security import RetellSignatureVerifier

class FakeEmailSender:
    """Records what the email node handed the provider.

    The executor resolves a provider per clinic, so tests assert against the
    message the sender received rather than against one vendor's HTTP payload.
    """

    def __init__(self, provider="resend", fail_with=None, fail_times=0):
        self.provider = provider
        self.sent = []
        self._fail_with = fail_with
        self._fail_times = fail_times
        self.attempts = 0

    async def send(self, message):
        self.attempts += 1
        if self._fail_with is not None and self.attempts <= self._fail_times:
            raise self._fail_with
        self.sent.append(message)
        from src.app.services.email.sender import EmailSendResult

        return EmailSendResult(
            provider=self.provider, provider_message_id=f"msg-{self.attempts}"
        )

    @property
    def last(self):
        return self.sent[-1] if self.sent else None


def make_resolved_identity(**overrides):
    """A verified clinic sending identity, as the executor would resolve one."""
    from src.app.services.email.identity_service import ResolvedSendingIdentity

    defaults = dict(
        from_address="clinic@example.com",
        from_name="Clinic",
        reply_to=None,
        provider="resend",
        tenant_name=None,
        configuration_set=None,
        is_platform_fallback=False,
        identity_id="identity-1",
    )
    defaults.update(overrides)
    return ResolvedSendingIdentity(**defaults)


@pytest.fixture
def mock_settings():
    """Mock application settings for unit tests."""
    return Settings(
        nexhealth_api_key="test-api-key",
        nexhealth_subdomain="test-subdomain",
        nexhealth_location_id="123",
        retell_api_secret="test-secret",
        jwt_secret="test-jwt-secret",
        app_env="test"
    )

@pytest.fixture
def retell_verifier(mock_settings):
    """Retell signature verifier fixture."""
    return RetellSignatureVerifier(mock_settings.retell_api_secret)

@pytest_asyncio.fixture
async def nh_client():
    """Real NexHealth client for integration tests."""
    async with NexHealthClient(settings) as client:
        yield client

@pytest_asyncio.fixture
async def async_client():
    """Async client for testing APIs."""
    # Initialize the client manually as we're not running full server startup events in this fixture style sometimes
    await init_nexhealth_client()
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
        
    await cleanup_nexhealth_client()

@pytest.fixture(autouse=True)
def mock_audit_service():
    """Install an in-memory audit service for every test.

    Audit writes are durable in production: ``service.log`` raises on
    repository failure (e.g. uninitialized DB), which would surface as 500s
    in any test that exercises a PHI-touching route. Routing every test to
    an in-memory repo gives us realistic durable-audit behavior without
    requiring the test DB to be initialized.

    Tests that need to inspect audit entries can grab the returned service
    directly; tests that need to assert failure paths can replace the repo
    via ``service._repository = ...``.
    """
    from src.app.services.audit import InMemoryAuditRepository, AuditService, set_audit_service
    repo = InMemoryAuditRepository()
    service = AuditService(repo)
    set_audit_service(service)
    return service


@pytest.fixture
def audit_log_entries(mock_audit_service):
    """Return persisted in-memory audit entries after pending background writes."""

    async def _get_entries():
        from src.app.services.audit import AuditService

        await AuditService.drain_background_tasks()
        return mock_audit_service._repository.get_all()

    return _get_entries


# ---------------------------------------------------------------------------
# Database-shaped mocks that refuse to lie
# ---------------------------------------------------------------------------


class BlindQueryError(AssertionError):
    """A mocked session was queried without the test saying what comes back.

    Raised instead of returning an empty result, because "no rows" is the most
    dangerous default a database mock can have: it is indistinguishable from a
    real empty table, from a WHERE clause that matches nothing, and — the case
    that motivated this — from an RLS context the policies do not recognise, so
    every query silently returns zero rows.

    That last one shipped. The Test Suite's ``/targets`` endpoint answered
    ``200 {"count": 0}`` on staging while five locations sat in the table, and
    31 unit tests passed throughout, because each of them handed the route an
    ``AsyncMock`` that cheerfully returned nothing.

    If you see this, the fix is to say what the query returns — see
    :func:`db_result` — not to make it return nothing.
    """


def db_result(*, scalars=(), one=None, scalar=None):
    """A stand-in for a SQLAlchemy ``Result``.

    ``scalars`` feeds ``.scalars().all()`` and ``.scalars().first()``; ``one``
    feeds ``.one_or_none()``; ``scalar`` feeds ``.scalar()``. Pass an empty
    ``scalars=[]`` deliberately when "no rows" is the thing under test — being
    explicit is the whole point.
    """
    from unittest.mock import MagicMock

    rows = list(scalars)
    result = MagicMock(name="db_result")
    result.scalars.return_value.all.return_value = rows
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    result.one_or_none.return_value = one
    result.scalar.return_value = scalar
    result.all.return_value = rows
    return result


def strict_db_session(execute=None, **attrs):
    """An async session mock whose ``execute`` must be answered explicitly.

    Pass ``execute`` as a result, a list of results (consumed in order), or a
    callable taking the statement — the callable form is what you want when the
    route runs several different queries, because dispatching on the statement
    survives someone adding a query in between, where positional stubs do not.
    """
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock(name="strict_db_session")
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    if execute is None:
        async def _refuse(statement, *a, **k):
            raise BlindQueryError(
                "This session was queried but the test never said what the "
                "database returns, so it would have silently answered 'no "
                "rows'. Pass execute=db_result(...) — or an explicit "
                "db_result(scalars=[]) if emptiness is what you are testing.\n"
                f"  statement: {str(statement)[:200]}"
            )
        session.execute = AsyncMock(side_effect=_refuse)
    elif callable(execute) and not hasattr(execute, "scalars"):
        session.execute = AsyncMock(side_effect=execute)
    elif isinstance(execute, (list, tuple)):
        session.execute = AsyncMock(side_effect=list(execute))
    else:
        session.execute = AsyncMock(return_value=execute)

    for key, value in attrs.items():
        setattr(session, key, value)
    return session
