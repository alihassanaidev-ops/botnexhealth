"""Unit tests for the NullPool branch of init_database.

Why this exists: Celery prefork workers run each task inside its own
``asyncio.run()`` loop. Pooled asyncpg connections bind to the loop they
were created on, so the second task in a worker crashes with
``RuntimeError: ... attached to a different loop``. ``init_database(...,
use_null_pool=True)`` is the production fix — these tests pin its
behavior so a refactor cannot silently regress.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy.pool import NullPool


@pytest.fixture(autouse=True)
def _reset_engine():
    """Each test reinitializes the module-global engine; reset between runs."""
    import src.app.database as database_mod

    importlib.reload(database_mod)
    yield
    importlib.reload(database_mod)


def test_init_database_default_uses_pooled_engine() -> None:
    """The API process keeps full pooling (no behavior change for FastAPI)."""
    import src.app.database as database_mod

    database_mod.init_database("postgresql+asyncpg://x:y@localhost/z")
    assert database_mod._engine is not None
    assert not isinstance(database_mod._engine.pool, NullPool)


def test_init_database_null_pool_uses_nullpool() -> None:
    """Celery worker path opts into NullPool to avoid cross-loop reuse."""
    import src.app.database as database_mod

    database_mod.init_database(
        "postgresql+asyncpg://x:y@localhost/z",
        use_null_pool=True,
    )
    assert database_mod._engine is not None
    assert isinstance(database_mod._engine.pool, NullPool)


def test_worker_process_init_signal_uses_null_pool(monkeypatch) -> None:
    """The post-fork signal handler must call init_database with use_null_pool=True."""
    import src.app.database as database_mod
    import src.app.worker as worker_mod

    captured: dict[str, object] = {}

    def fake_init(database_url: str, *, use_null_pool: bool = False) -> None:
        captured["database_url"] = database_url
        captured["use_null_pool"] = use_null_pool

    monkeypatch.setattr(database_mod, "init_database", fake_init)
    monkeypatch.setattr(database_mod, "is_database_initialized", lambda: False)
    monkeypatch.setattr(worker_mod.settings, "database_url", "postgresql+asyncpg://u:p@h/d")

    worker_mod._init_database_in_worker_process()

    assert captured == {
        "database_url": "postgresql+asyncpg://u:p@h/d",
        "use_null_pool": True,
    }
