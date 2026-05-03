import sys
import os

# Add project root to python path before importing src
sys.path.append(os.getcwd())

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.app.config import Settings, settings
from src.app.retell.security import RetellSignatureVerifier
from src.app.nexhealth.client import NexHealthClient
from src.app.main import app

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

from src.app.dependencies import get_nexhealth_client_dependency, init_nexhealth_client, cleanup_nexhealth_client

@pytest_asyncio.fixture
async def async_client():
    """Async client for testing APIs."""
    # Initialize the client manually as we're not running full server startup events in this fixture style sometimes
    await init_nexhealth_client()
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
        
    await cleanup_nexhealth_client()

@pytest.fixture
def mock_audit_service():
    """Ensure tests don't try to use PostgresAuditRepository."""
    from src.app.services.audit import InMemoryAuditRepository, AuditService, set_audit_service
    repo = InMemoryAuditRepository()
    service = AuditService(repo)
    set_audit_service(service)
    return service
