"""Keep ``workflow_schedules`` in step with published campaigns, and claim due ticks.

Two halves:

* **Sync.** On publish/pause/resume/archive, rewrite a campaign's schedule rows
  so they match its current definition. Rows are keyed by (workflow, location)
  and carry a cursor, so re-syncing an unchanged schedule must not reset when it
  next fires — that would let a republish skip or repeat a tick.
* **Claim.** One beat task takes the due rows with ``FOR UPDATE SKIP LOCKED``,
  the same way workflow timers are claimed, and advances each cursor before
  doing any work.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationWorkflow,
    AutomationWorkflowStatus,
)
from src.app.models.institution_location import InstitutionLocation
from src.app.models.workflow_schedule import WorkflowSchedule
from src.app.services.automation.definition_schema import (
    ScheduleTrigger,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)

#: Ceiling on rows taken per beat, so one tenant's fan-out cannot monopolise a
#: cycle. The claim is ordered by due time, so anything skipped is taken next.
CLAIM_BATCH = 50


def _safe_zone(name: str | None) -> ZoneInfo:
    """The location's zone, or UTC if it is missing or unknown.

    A campaign firing at the wrong hour is a much smaller problem than one that
    never fires because a timezone string was mistyped.
    """
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r; scheduling in UTC", name)
        return ZoneInfo("UTC")


def next_fire_after(cron: str, *, after: datetime, timezone_name: str) -> datetime:
    """The first firing strictly after ``after``, in ``timezone_name``.

    Evaluated in local time so "every weekday at 9" tracks the clinic's clock
    across a DST change rather than drifting an hour twice a year. The result is
    converted back to UTC, which is what the claim query compares.
    """
    zone = _safe_zone(timezone_name)
    local_after = after.astimezone(zone)
    nxt = croniter(cron, local_after).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=zone)
    return nxt.astimezone(timezone.utc)


def schedule_triggers(definition: WorkflowDefinition) -> list[ScheduleTrigger]:
    return [t for t in definition.triggers if isinstance(t, ScheduleTrigger)]


class WorkflowScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def sync_for_workflow(
        self,
        workflow: AutomationWorkflow,
        *,
        now: datetime | None = None,
    ) -> int:
        """Rewrite this campaign's schedule rows. Returns how many are active.

        Safe to call on every publish. An unchanged cron keeps its cursor; a
        changed one is recomputed from now, because the old cursor referred to a
        schedule that no longer exists.
        """
        now = now or datetime.now(tz=timezone.utc)
        existing = {
            (row.location_id): row
            for row in (
                await self.session.execute(
                    select(WorkflowSchedule).where(
                        WorkflowSchedule.workflow_id == str(workflow.id)
                    )
                )
            ).scalars()
        }

        definition_json = workflow.definition
        triggers: list[ScheduleTrigger] = []
        if definition_json:
            try:
                triggers = schedule_triggers(
                    WorkflowDefinition.model_validate(definition_json)
                )
            except Exception:
                logger.exception(
                    "schedule sync could not read definition for workflow %s",
                    workflow.id,
                )
                triggers = []

        publishable = (
            workflow.status == AutomationWorkflowStatus.ACTIVE.value
            and workflow.current_version_id is not None
            and bool(triggers)
        )
        if not publishable:
            # Keep the rows but stop them firing, so resuming a paused campaign
            # does not lose where it had got to.
            for row in existing.values():
                row.is_active = False
            await self.session.flush()
            return 0

        # One schedule per campaign for now: the builder authors a single
        # trigger, and two schedules on one campaign would need a rule for which
        # one a tick belongs to.
        trigger = triggers[0]
        locations = await self._target_locations(workflow)
        if not locations:
            for row in existing.values():
                row.is_active = False
            await self.session.flush()
            return 0

        active = 0
        for location in locations:
            zone_name = (
                trigger.fixed_timezone
                if trigger.timezone_mode == "fixed" and trigger.fixed_timezone
                else (location.timezone or "UTC")
            )
            row = existing.pop(str(location.id), None)
            if row is None:
                self.session.add(
                    WorkflowSchedule(
                        institution_id=str(workflow.institution_id),
                        location_id=str(location.id),
                        workflow_id=str(workflow.id),
                        workflow_version_id=str(workflow.current_version_id),
                        cron=trigger.cron,
                        timezone=zone_name,
                        next_fire_at=next_fire_after(
                            trigger.cron, after=now, timezone_name=zone_name
                        ),
                    )
                )
            else:
                rescheduled = row.cron != trigger.cron or row.timezone != zone_name
                row.cron = trigger.cron
                row.timezone = zone_name
                row.workflow_version_id = str(workflow.current_version_id)
                row.is_active = True
                if rescheduled or row.next_fire_at is None:
                    row.next_fire_at = next_fire_after(
                        trigger.cron, after=now, timezone_name=zone_name
                    )
            active += 1

        # Locations the campaign no longer targets.
        for stale in existing.values():
            await self.session.execute(
                delete(WorkflowSchedule).where(WorkflowSchedule.id == stale.id)
            )

        await self.session.flush()
        return active

    async def _target_locations(
        self, workflow: AutomationWorkflow
    ) -> list[InstitutionLocation]:
        """Locations this campaign runs at: its own, or every active one."""
        stmt = select(InstitutionLocation).where(
            InstitutionLocation.institution_id == str(workflow.institution_id)
        )
        if workflow.location_id is not None:
            stmt = stmt.where(InstitutionLocation.id == str(workflow.location_id))
        elif hasattr(InstitutionLocation, "is_active"):
            stmt = stmt.where(InstitutionLocation.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = CLAIM_BATCH,
    ) -> list[WorkflowSchedule]:
        """Take due schedules and advance their cursors in one transaction.

        The cursor moves *before* any enrollment work, so a crash mid-fan-out
        loses one tick rather than replaying it forever. Advancing past every
        slot already in the past is what stops a beat outage from firing a
        backlog of catch-up ticks all at once.
        """
        now = now or datetime.now(tz=timezone.utc)
        rows = list(
            (
                await self.session.execute(
                    select(WorkflowSchedule)
                    .where(
                        WorkflowSchedule.is_active.is_(True),
                        WorkflowSchedule.next_fire_at <= now,
                    )
                    .order_by(WorkflowSchedule.next_fire_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )

        for row in rows:
            row.last_fired_at = now
            row.next_fire_at = next_fire_after(
                row.cron, after=now, timezone_name=row.timezone
            )
        await self.session.flush()
        return rows
