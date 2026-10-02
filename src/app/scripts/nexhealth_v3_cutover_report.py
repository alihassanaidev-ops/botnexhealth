"""Generate NexHealth v3 cutover baseline and monitoring reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.app.database import (
    get_superadmin_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.nexhealth.api_contract import normalize_nexhealth_api_contract

logger = logging.getLogger(__name__)

_DEFAULT_MONITORING_WINDOW_HOURS = 24
_DEFAULT_MIN_STABLE_DAYS = 7


async def run_report(
    *,
    baseline_path: Path | None = None,
    save_snapshot_path: Path | None = None,
    monitoring_window_hours: int = _DEFAULT_MONITORING_WINDOW_HOURS,
    stable_since: str | None = None,
    min_stable_days: int = _DEFAULT_MIN_STABLE_DAYS,
    v2_overlap_removed: bool = False,
) -> dict[str, Any]:
    from src.app.config import settings
    from src.app.services.automation.nexhealth_cutover_service import (
        NexHealthCutoverService,
        assess_cutover,
        assessment_to_dict,
        parse_iso_datetime,
        snapshot_to_dict,
    )

    _ensure_db()
    contract = normalize_nexhealth_api_contract(settings.nexhealth_api_version)
    async with get_superadmin_system_db_session(
        "nexhealth_v3_cutover_report"
    ) as session:
        snapshot = await NexHealthCutoverService(
            session,
            app_env=settings.app_env,
            api_contract=contract,
        ).collect_snapshot(monitoring_window_hours=monitoring_window_hours)

    if save_snapshot_path:
        save_snapshot_path.write_text(
            json.dumps(snapshot_to_dict(snapshot), indent=2, sort_keys=True) + "\n"
        )

    baseline = _load_snapshot(baseline_path) if baseline_path else None
    assessment = assess_cutover(
        snapshot,
        baseline=baseline,
        stable_since=parse_iso_datetime(stable_since),
        min_stable_days=min_stable_days,
        v2_overlap_removed=v2_overlap_removed,
    )
    return {
        "snapshot": snapshot_to_dict(snapshot),
        "baseline": snapshot_to_dict(baseline) if baseline else None,
        "assessment": assessment_to_dict(assessment),
    }


def _load_snapshot(path: Path) -> Any:
    from src.app.services.automation.nexhealth_cutover_service import snapshot_from_dict

    return snapshot_from_dict(json.loads(path.read_text()))


def _ensure_db() -> None:
    admin_url = _admin_database_url()
    if not admin_url:
        raise SystemExit("DATABASE_ADMIN_URL is required to collect a cutover report")
    if not is_database_initialized():
        init_database(admin_url, use_null_pool=True)
    if not is_database_initialized():
        raise SystemExit("DATABASE_ADMIN_URL is required to collect a cutover report")


def _admin_database_url() -> str | None:
    return os.environ.get("DATABASE_ADMIN_URL")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and compare NexHealth v3 cutover health snapshots."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Path to a pre-cutover snapshot JSON to compare against.",
    )
    parser.add_argument(
        "--save-snapshot",
        type=Path,
        help="Write the current snapshot JSON to this path.",
    )
    parser.add_argument(
        "--monitoring-window-hours",
        type=int,
        default=_DEFAULT_MONITORING_WINDOW_HOURS,
        help="Recent window for audit/webhook failure counters.",
    )
    parser.add_argument(
        "--stable-since",
        help="ISO timestamp when production became stable on v3.",
    )
    parser.add_argument(
        "--min-stable-days",
        type=int,
        default=_DEFAULT_MIN_STABLE_DAYS,
        help="Minimum stable-v3 days required before cleanup readiness.",
    )
    parser.add_argument(
        "--v2-overlap-removed",
        action="store_true",
        help="Operator confirmation that old v2-pinned webhook subscriptions are gone.",
    )
    parser.add_argument(
        "--fail-on-rollback-signal",
        action="store_true",
        help="Exit with code 2 when the assessment recommends rollback.",
    )
    return parser


def main() -> int:
    logging.basicConfig(level="INFO")
    args = _parser().parse_args()
    report = asyncio.run(
        run_report(
            baseline_path=args.baseline,
            save_snapshot_path=args.save_snapshot,
            monitoring_window_hours=args.monitoring_window_hours,
            stable_since=args.stable_since,
            min_stable_days=args.min_stable_days,
            v2_overlap_removed=args.v2_overlap_removed,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_rollback_signal and report["assessment"]["rollback_recommended"]:
        logger.error("NexHealth cutover rollback signal detected")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
