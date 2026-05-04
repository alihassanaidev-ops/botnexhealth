import pytest

from src.app.scripts import bootstrap_database


@pytest.mark.asyncio
async def test_bootstrap_skips_when_application_tables_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    created = False

    async def fake_list_tables(_: str) -> set[str]:
        return {"users", "institutions"}

    async def fake_create_schema(_: str) -> None:
        nonlocal created
        created = True

    monkeypatch.setattr(bootstrap_database, "_list_tables", fake_list_tables)
    monkeypatch.setattr(bootstrap_database, "_create_schema", fake_create_schema)

    bootstrapped = await bootstrap_database.bootstrap_database_if_empty("postgresql+asyncpg://example")

    assert bootstrapped is False
    assert created is False


@pytest.mark.asyncio
async def test_bootstrap_creates_schema_for_empty_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_list_tables(_: str) -> set[str]:
        calls.append("list")
        return set()

    async def fake_create_schema(_: str) -> None:
        calls.append("create")

    monkeypatch.setattr(bootstrap_database, "_list_tables", fake_list_tables)
    monkeypatch.setattr(bootstrap_database, "_create_schema", fake_create_schema)

    bootstrapped = await bootstrap_database.bootstrap_database_if_empty("postgresql+asyncpg://example")

    assert bootstrapped is True
    assert calls == ["list", "create"]


@pytest.mark.asyncio
async def test_bootstrap_treats_alembic_version_only_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_list_tables(_: str) -> set[str]:
        calls.append("list")
        return {"alembic_version"}

    async def fake_create_schema(_: str) -> None:
        calls.append("create")

    monkeypatch.setattr(bootstrap_database, "_list_tables", fake_list_tables)
    monkeypatch.setattr(bootstrap_database, "_create_schema", fake_create_schema)

    bootstrapped = await bootstrap_database.bootstrap_database_if_empty("postgresql+asyncpg://example")

    assert bootstrapped is True
    assert calls == ["list", "create"]


@pytest.mark.asyncio
async def test_create_schema_applies_audit_log_hardening_after_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeConnection:
        async def run_sync(self, fn: object, **kwargs: object) -> None:
            calls.append(("create_all", kwargs))

        async def execute(self, statement: object) -> None:
            calls.append(("execute", str(statement)))

    class FakeBegin:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, *_: object) -> None:
            return None

    class FakeEngine:
        def begin(self) -> FakeBegin:
            return FakeBegin()

        async def dispose(self) -> None:
            calls.append(("dispose", None))

    monkeypatch.setattr(
        bootstrap_database,
        "create_async_engine",
        lambda *_, **__: FakeEngine(),
    )

    await bootstrap_database._create_schema("postgresql+asyncpg://example")

    assert calls[0] == ("create_all", {"checkfirst": True})
    hardening_sql = "\n".join(
        value for action, value in calls if action == "execute"
    )
    assert "CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()" in hardening_sql
    assert "CREATE TRIGGER audit_logs_no_update" in hardening_sql
    assert "BEFORE UPDATE ON audit_logs" in hardening_sql
    assert "CREATE TRIGGER audit_logs_no_delete" in hardening_sql
    assert "BEFORE DELETE ON audit_logs" in hardening_sql
    assert "ADD CONSTRAINT audit_logs_actor_check" in hardening_sql
    assert (
        "CHECK (actor IN ('RETELL_AGENT', 'ADMIN', 'SYSTEM', 'API_CLIENT'))"
        in hardening_sql
    )
    assert calls[-1] == ("dispose", None)


def test_ensure_database_bootstrapped_stamps_head_after_async_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_asyncio_run(coro: object) -> bool:
        calls.append("run")
        coro.close()
        return True

    def fake_stamp_head() -> None:
        calls.append("stamp")

    monkeypatch.setattr(bootstrap_database.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(bootstrap_database, "_stamp_head", fake_stamp_head)

    bootstrapped = bootstrap_database.ensure_database_bootstrapped("postgresql+asyncpg://example")

    assert bootstrapped is True
    assert calls == ["run", "stamp"]


def test_ensure_database_bootstrapped_skips_stamp_when_async_bootstrap_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_asyncio_run(coro: object) -> bool:
        calls.append("run")
        coro.close()
        return False

    def fake_stamp_head() -> None:
        calls.append("stamp")

    monkeypatch.setattr(bootstrap_database.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(bootstrap_database, "_stamp_head", fake_stamp_head)

    bootstrapped = bootstrap_database.ensure_database_bootstrapped("postgresql+asyncpg://example")

    assert bootstrapped is False
    assert calls == ["run"]
