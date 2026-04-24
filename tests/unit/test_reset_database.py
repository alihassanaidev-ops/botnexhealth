import pytest

from src.app.scripts import reset_database


def test_main_requires_explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(reset_database.os, "getenv", lambda _: None)

    with pytest.raises(SystemExit, match="ALLOW_DESTRUCTIVE_RESET"):
        reset_database.main()


def test_main_resets_when_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_asyncio_run(coro: object) -> None:
        calls.append("run")
        coro.close()

    monkeypatch.setattr(reset_database.settings, "database_url", "postgresql+asyncpg://example")
    monkeypatch.setattr(reset_database.os, "getenv", lambda _: "1")
    monkeypatch.setattr(reset_database.asyncio, "run", fake_asyncio_run)

    exit_code = reset_database.main()

    assert exit_code == 0
    assert calls == ["run"]
