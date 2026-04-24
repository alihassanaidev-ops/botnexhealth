import pytest

from src.app.scripts import migrate_database


def test_main_bootstraps_empty_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(migrate_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(migrate_database, "ensure_database_bootstrapped", lambda _: True)
    monkeypatch.setattr(migrate_database, "_upgrade_head", lambda: calls.append("upgrade"))

    exit_code = migrate_database.main()

    assert exit_code == 0
    assert calls == []


def test_main_requires_baseline_for_untracked_existing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_asyncio_run(coro: object) -> list[str]:
        calls.append("run")
        coro.close()
        return []

    monkeypatch.setattr(migrate_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(migrate_database, "ensure_database_bootstrapped", lambda _: False)
    monkeypatch.setattr(migrate_database.asyncio, "run", fake_asyncio_run)

    with pytest.raises(SystemExit, match="ALEMBIC_BASELINE_REVISION"):
        migrate_database.main()

    assert calls == ["run"]


def test_main_stamps_baseline_then_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    revisions = [[], ["20260330_local_auth"]]

    def fake_asyncio_run(coro: object) -> list[str]:
        calls.append("run")
        coro.close()
        return revisions.pop(0)

    monkeypatch.setattr(migrate_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(migrate_database, "ensure_database_bootstrapped", lambda _: False)
    monkeypatch.setattr(migrate_database.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(migrate_database.os, "getenv", lambda key: "20260301_rename_tenant_to_institution")
    monkeypatch.setattr(migrate_database, "_stamp_revision", lambda revision: calls.append(f"stamp:{revision}"))
    monkeypatch.setattr(migrate_database, "_upgrade_head", lambda: calls.append("upgrade"))

    exit_code = migrate_database.main()

    assert exit_code == 0
    assert calls == [
        "run",
        "stamp:20260301_rename_tenant_to_institution",
        "upgrade",
        "run",
    ]


def test_main_upgrades_tracked_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    revisions = [["20260301_rename_tenant_to_institution"], ["20260330_local_auth"]]

    def fake_asyncio_run(coro: object) -> list[str]:
        calls.append("run")
        coro.close()
        return revisions.pop(0)

    monkeypatch.setattr(migrate_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(migrate_database, "ensure_database_bootstrapped", lambda _: False)
    monkeypatch.setattr(migrate_database.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(migrate_database, "_stamp_revision", lambda revision: calls.append(f"stamp:{revision}"))
    monkeypatch.setattr(migrate_database, "_upgrade_head", lambda: calls.append("upgrade"))

    exit_code = migrate_database.main()

    assert exit_code == 0
    assert calls == ["run", "upgrade", "run"]
