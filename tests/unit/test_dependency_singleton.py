import asyncio

import pytest
from src.app.dependencies import (
    NexHealthCredentialContext,
    cleanup_nexhealth_client,
    get_nexhealth_client_dependency,
    get_nexhealth_client_for_credential,
    init_nexhealth_client,
    scoped_nexhealth_clients,
)

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


def test_scoped_nexhealth_clients_do_not_cross_asyncio_run_loops(monkeypatch):
    class FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeNexHealthClient:
        instances = []

        def __init__(self, config, token_manager=None, rate_limiter=None):
            self.config = config
            self.token_manager = token_manager
            self.rate_limiter = rate_limiter
            self.closed = False
            FakeNexHealthClient.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.closed = True

    rate_redis_instances = []
    token_redis_instances = []

    def fake_rate_limiter():
        redis = FakeRedis()
        rate_redis_instances.append(redis)
        return object(), redis

    def fake_token_manager(api_key_hash):
        redis = FakeRedis()
        token_redis_instances.append(redis)
        return object(), redis

    monkeypatch.setattr("src.app.dependencies.NexHealthClient", FakeNexHealthClient)
    monkeypatch.setattr(
        "src.app.dependencies._build_nexhealth_rate_limiter", fake_rate_limiter
    )
    monkeypatch.setattr(
        "src.app.dependencies._build_nexhealth_token_manager", fake_token_manager
    )

    credential = NexHealthCredentialContext(
        mode="platform",
        api_key="test-key",
        api_key_hash="test-key-hash",
    )

    async def get_scoped_client():
        async with scoped_nexhealth_clients():
            first = await get_nexhealth_client_for_credential(credential)
            second = await get_nexhealth_client_for_credential(credential)
            assert first is second
            assert not first.closed
            return first

    first_loop_client = asyncio.run(get_scoped_client())
    second_loop_client = asyncio.run(get_scoped_client())

    assert first_loop_client is not second_loop_client
    assert first_loop_client.closed
    assert second_loop_client.closed
    assert len(FakeNexHealthClient.instances) == 2
    assert all(redis.closed for redis in rate_redis_instances)
    assert all(redis.closed for redis in token_redis_instances)
