"""Static safety checks for cancelled Retell SMS session repair."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260828_retell_sms_cancelled_sessions.py"


def test_retell_sms_cancel_migration_chains_after_current_head() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260828_retell_sms_cancel"' in source
    assert 'down_revision = "20260827_execution_snapshots"' in source


def test_retell_sms_cancel_migration_repairs_stale_active_sessions() -> None:
    source = MIGRATION.read_text()

    assert "'cancelled'" in source
    assert "terminal_outcome = 'workflow_cancelled'" in source
    assert "run.status = 'cancelled'" in source
    assert "session.status IN ('awaiting_user', 'generating')" in source
