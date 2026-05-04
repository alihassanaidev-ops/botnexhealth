"""Tests for the simplified migrate_database wrapper."""

from __future__ import annotations

import pytest

from src.app.scripts import migrate_database


def test_main_runs_alembic_upgrade_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrate_database.settings, "database_url", "runtime")
    monkeypatch.delenv("ALEMBIC_BASELINE_REVISION", raising=False)
    called = False

    def fake_upgrade() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(migrate_database, "upgrade_to_head", fake_upgrade)

    assert migrate_database.main() == 0
    assert called is True


def test_main_stamps_baseline_before_upgrade_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migrate_database.settings, "database_url", "runtime")
    monkeypatch.setenv("ALEMBIC_BASELINE_REVISION", "20260510_baseline")
    sequence: list[str] = []

    def fake_stamp(cfg, revision):  # noqa: ANN001
        sequence.append(f"stamp:{revision}")

    def fake_upgrade() -> None:
        sequence.append("upgrade")

    from alembic import command as alembic_command

    monkeypatch.setattr(alembic_command, "stamp", fake_stamp)
    monkeypatch.setattr(migrate_database, "upgrade_to_head", fake_upgrade)

    assert migrate_database.main() == 0
    assert sequence == ["stamp:20260510_baseline", "upgrade"]


def test_main_raises_when_database_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrate_database.settings, "database_url", "")
    with pytest.raises(SystemExit):
        migrate_database.main()
