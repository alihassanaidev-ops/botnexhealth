"""Audit (and optionally pause) workflows whose trigger/nodes belong to a PMS
the owning institution does not run.

Before PMS-scope validation existed, an institution on NexHealth could publish
an ``appointment_state_changed`` campaign. It never enrolls anyone — the only
event source for that trigger is the GoTracker webhook — so pausing it is safe
and makes the new blocking launch-checklist/validation message visible instead
of leaving a silently dead campaign looking active.

    python -m src.app.scripts.audit_pms_scope_mismatch            # report only
    python -m src.app.scripts.audit_pms_scope_mismatch --pause    # also pause
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.automation_workflow import (
    AutomationWorkflow,
    AutomationWorkflowStatus,
)
from src.app.models.institution import Institution
from src.app.services.automation import pms_scope

logger = logging.getLogger(__name__)


async def run(pause: bool) -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AutomationWorkflow, Institution.pms_type)
                    .join(
                        Institution,
                        Institution.id == AutomationWorkflow.institution_id,
                    )
                    .where(
                        AutomationWorkflow.status.in_(
                            [
                                AutomationWorkflowStatus.ACTIVE.value,
                                AutomationWorkflowStatus.PAUSED.value,
                            ]
                        )
                    )
                )
            )
            .unique()
            .all()
        )

        mismatched = 0
        for workflow, pms_type in rows:
            definition = workflow.definition or {}
            trigger_type = (definition.get("trigger") or {}).get("type") or ""
            bad_trigger = trigger_type and not pms_scope.trigger_allowed(
                trigger_type, pms_type
            )
            bad_nodes = sorted(
                {
                    node.get("type", "")
                    for node in definition.get("nodes", [])
                    if not pms_scope.node_allowed(node.get("type", ""), pms_type)
                }
            )
            if not bad_trigger and not bad_nodes:
                continue
            mismatched += 1
            reason = []
            if bad_trigger:
                reason.append(f"trigger={trigger_type}")
            if bad_nodes:
                reason.append(f"nodes={','.join(bad_nodes)}")
            print(
                f"[{workflow.status}] {workflow.id} "
                f'"{workflow.name}" institution={workflow.institution_id} '
                f"pms={pms_type} mismatch: {'; '.join(reason)}"
            )
            if pause and workflow.status == AutomationWorkflowStatus.ACTIVE.value:
                workflow.status = AutomationWorkflowStatus.PAUSED.value
                print("  -> paused")

        if pause:
            await session.commit()
        print(f"\n{mismatched} mismatched workflow(s) out of {len(rows)} checked.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Pause active mismatched workflows (default: report only).",
    )
    args = parser.parse_args()
    asyncio.run(run(pause=args.pause))


if __name__ == "__main__":
    main()
