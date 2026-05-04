"""Tests for the simplified bootstrap_database wrapper.

After the consolidated baseline migration (20260510_baseline) bootstrap
is a thin shim around ``alembic upgrade head``. There's no
``Base.metadata.create_all`` path anymore — alembic is the single source
of truth for schema, so the tests are correspondingly small.
"""

from __future__ import annotations

import pytest

from src.app.scripts import bootstrap_database


def test_admin_url_prefers_database_admin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql+asyncpg://admin@host/db")
    monkeypatch.setattr(bootstrap_database.settings, "database_url", "runtime")

    assert bootstrap_database._admin_database_url() == "postgresql+asyncpg://admin@host/db"


def test_admin_url_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setattr(bootstrap_database.settings, "database_url", "runtime")

    assert bootstrap_database._admin_database_url() == "runtime"


def test_upgrade_to_head_invokes_alembic_with_admin_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_upgrade(cfg, revision):  # noqa: ANN001
        captured["url"] = cfg.get_main_option("sqlalchemy.url")
        captured["revision"] = revision

    monkeypatch.setattr(bootstrap_database.command, "upgrade", fake_upgrade)
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql+asyncpg://admin@host/db")

    bootstrap_database.upgrade_to_head()

    assert captured["url"] == "postgresql+asyncpg://admin@host/db"
    assert captured["revision"] == "head"


def test_upgrade_to_head_raises_without_any_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setattr(bootstrap_database.settings, "database_url", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        bootstrap_database.upgrade_to_head()


def test_main_skips_when_database_url_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bootstrap_database.settings, "database_url", "")
    called = False

    def fake_upgrade():  # noqa: ANN001
        nonlocal called
        called = True

    monkeypatch.setattr(bootstrap_database, "upgrade_to_head", fake_upgrade)

    assert bootstrap_database.main() == 0
    assert called is False
