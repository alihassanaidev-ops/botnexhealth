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
