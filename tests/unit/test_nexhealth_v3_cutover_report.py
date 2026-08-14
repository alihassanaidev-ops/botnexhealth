from __future__ import annotations

import pytest

from src.app.scripts import nexhealth_v3_cutover_report


def test_cutover_report_requires_database_admin_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setattr(
        nexhealth_v3_cutover_report, "is_database_initialized", lambda: False
    )

    with pytest.raises(SystemExit, match="DATABASE_ADMIN_URL"):
        nexhealth_v3_cutover_report._ensure_db()


def test_cutover_report_initializes_with_database_admin_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_is_initialized() -> bool:
        return bool(captured)

    def fake_init_database(database_url: str, *, use_null_pool: bool = False) -> None:
        captured["database_url"] = database_url
        captured["use_null_pool"] = use_null_pool

    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql+asyncpg://admin@host/db")
    monkeypatch.setattr(
        nexhealth_v3_cutover_report,
        "is_database_initialized",
        fake_is_initialized,
    )
    monkeypatch.setattr(
        nexhealth_v3_cutover_report,
        "init_database",
        fake_init_database,
    )

    nexhealth_v3_cutover_report._ensure_db()

    assert captured == {
        "database_url": "postgresql+asyncpg://admin@host/db",
        "use_null_pool": True,
    }
