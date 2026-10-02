"""Replay NexHealth appointment workflow matching from the local projection.

The command is dry-run by default. It reuses the normal Celery trigger task, so
workflow filters, timing rules, location scope, and enrollment idempotency all
remain authoritative.

    python -m src.app.scripts.replay_nexhealth_appointment_triggers \
        --institution-id UUID
    python -m src.app.scripts.replay_nexhealth_appointment_triggers \
        --institution-id UUID --appointment-id 1683218907 --enqueue
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.institution import Institution


async def run(
    *,
    institution_id: str,
    location_id: str | None,
    appointment_id: str | None,
    lookahead_days: int,
    enqueue: bool,
) -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            institution = await session.get(Institution, institution_id)
            if institution is None:
                raise SystemExit(f"Institution not found: {institution_id}")
            if institution.pms_type != "nexhealth":
                raise SystemExit(
                    f"Institution {institution_id} uses {institution.pms_type}, not NexHealth"
                )

            query = select(AppointmentWorkingSet).where(
                AppointmentWorkingSet.institution_id == institution_id,
                AppointmentWorkingSet.status == "scheduled",
            )
            if location_id:
                query = query.where(AppointmentWorkingSet.location_id == location_id)
            if appointment_id:
                query = query.where(
                    AppointmentWorkingSet.nexhealth_appointment_id.in_(
                        _appointment_id_variants(appointment_id)
                    )
                )
            else:
                now = datetime.now(tz=timezone.utc)
                query = query.where(
                    AppointmentWorkingSet.start_time >= now,
                    AppointmentWorkingSet.start_time
                    <= now + timedelta(days=lookahead_days),
                )

            rows = list(
                (
                    await session.execute(
                        query.order_by(AppointmentWorkingSet.start_time)
                    )
                )
                .scalars()
                .all()
            )

        print(
            f"{'ENQUEUE' if enqueue else 'DRY RUN'}: {len(rows)} scheduled "
            "NexHealth appointment(s) matched."
        )
        if enqueue:
            from src.app.tasks.automation_workflow import (
                trigger_appointment_workflows,
            )

        queued = 0
        for row in rows:
            appointment_key = str(row.nexhealth_appointment_id)
            print(
                f"  appointment={appointment_key} location={row.location_id} "
                f"start={row.start_time.isoformat() if row.start_time else 'missing'}"
            )
            if not enqueue or row.start_time is None:
                continue
            trigger_appointment_workflows.delay(
                institution_id=institution_id,
                appointment_id=appointment_key,
                appointment_at_iso=row.start_time.isoformat(),
                contact_id=str(row.contact_id) if row.contact_id else None,
                location_id=str(row.location_id) if row.location_id else None,
                trigger_metadata={
                    "event": "appointment_replay",
                    "source": "nexhealth_replay",
                    "pms_source": "nexhealth",
                    "appointment_status": "booked",
                    "appointment_reason": row.appointment_reason,
                    "appointment_type_id": row.appointment_type_id,
                    "nexhealth_payload": {
                        "event": "appointment_replay",
                        "appointment": {
                            "id": appointment_key.removeprefix("nh-"),
                            "appointment_type_id": row.appointment_type_id,
                            "appointment_type_name": row.appointment_reason,
                            "cancelled": False,
                        },
                    },
                },
            )
            queued += 1
        if enqueue:
            print(f"Queued {queued} appointment trigger task(s).")
    finally:
        await engine.dispose()


def _appointment_id_variants(value: str) -> list[str]:
    raw = value.removeprefix("nh-")
    return list(dict.fromkeys([value, raw, f"nh-{raw}"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--institution-id", required=True)
    parser.add_argument("--location-id")
    parser.add_argument("--appointment-id")
    parser.add_argument("--lookahead-days", type=int, default=30)
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Queue normal workflow trigger tasks (default: report only).",
    )
    args = parser.parse_args()
    if args.lookahead_days < 1 or args.lookahead_days > 365:
        parser.error("--lookahead-days must be between 1 and 365")
    asyncio.run(
        run(
            institution_id=args.institution_id,
            location_id=args.location_id,
            appointment_id=args.appointment_id,
            lookahead_days=args.lookahead_days,
            enqueue=args.enqueue,
        )
    )


if __name__ == "__main__":
    main()
