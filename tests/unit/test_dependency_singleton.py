
import pytest
from src.app.dependencies import get_nexhealth_client_dependency, init_nexhealth_client, cleanup_nexhealth_client
from src.app.config import settings

@pytest.mark.asyncio
async def test_singleton_client():
    # Setup
    await init_nexhealth_client()
    
    try:
        # Get client twice
        client1 = await get_nexhealth_client_dependency()
        client2 = await get_nexhealth_client_dependency()
        
        # Verify it's the exact same object (memory address)
        assert client1 is client2
        assert id(client1) == id(client2)
        
    finally:
        await cleanup_nexhealth_client()
