"""Enroll contacts into automation workflow runs with idempotency dedup."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
)

logger = logging.getLogger(__name__)

# Non-terminal run statuses — a contact already in one of these for a workflow
# must not be enrolled into a second, conflicting run of the same workflow.
_ACTIVE_RUN_STATUSES = (
    AutomationRunStatus.PENDING.value,
    AutomationRunStatus.RUNNING.value,
    AutomationRunStatus.WAITING.value,
)


class AutomationWorkflowEnrollmentService:
    """Creates and deduplicates AutomationWorkflowRun records.

    Compliance gate integration (Plan 12 ComplianceGateService) is a
    placeholder here — it will be wired in once the 01↔12 contract is frozen.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enroll(
        self,
        *,
        institution_id: str,
        workflow_id: str,
        workflow_version_id: str,
        contact_id: str | None = None,
        location_id: str | None = None,
        trigger_type: str | None = None,
        trigger_ref_type: str | None = None,
        trigger_ref_id: str | None = None,
        trigger_metadata: dict | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[AutomationWorkflowRun, bool]:
        """Enroll a contact into a workflow version.

        Returns (run, created). If idempotency_key is supplied and a run with
        that key already exists for this institution, the existing run is
        returned with created=False (no duplicate created).
        """
        if idempotency_key:
            existing = await self._find_by_idempotency(
                institution_id, workflow_version_id, idempotency_key
            )
            if existing is not None:
                return existing, False

        # Conflicting-active-run dedup: a contact already in a non-terminal run of
        # this workflow is not enrolled again (avoids double-contact). Re-enrollment
        # is allowed once the prior run reaches a terminal state.
        if contact_id is not None:
            conflicting = await self._find_active_run(
                institution_id, workflow_id, contact_id
            )
            if conflicting is not None:
                logger.info(
                    "enroll: contact %s already has active run %s for workflow %s",
                    contact_id, conflicting.id, workflow_id,
                )
                return conflicting, False

        run = AutomationWorkflowRun(
            institution_id=institution_id,
            location_id=location_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            contact_id=contact_id,
            idempotency_key=idempotency_key,
            trigger_type=trigger_type,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            trigger_metadata=trigger_metadata,
            status=AutomationRunStatus.PENDING.value,
        )
        self.session.add(run)
        try:
            # Savepoint: if a concurrent enrollment wins the race on the
            # (institution, version, idempotency_key) unique index, the failed
            # INSERT is rolled back to here without poisoning the outer
            # transaction, and we return the winner's run instead of 500ing.
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            if idempotency_key:
                existing = await self._find_by_idempotency(
                    institution_id, workflow_version_id, idempotency_key
                )
                if existing is not None:
                    logger.info(
                        "enroll: idempotency race resolved institution=%s key=%s",
                        institution_id, idempotency_key,
                    )
                    return existing, False
            raise

        self.session.add(
            AutomationWorkflowEvent(
                institution_id=institution_id,
                location_id=location_id,
                workflow_run_id=run.id,
                event_type="run.enrolled",
                event_metadata={
                    "trigger_type": trigger_type,
                    "trigger_ref_type": trigger_ref_type,
                    "trigger_ref_id": trigger_ref_id,
                },
            )
        )
        await self.session.flush()
        return run, True

    async def _find_active_run(
        self, institution_id: str, workflow_id: str, contact_id: str
    ) -> AutomationWorkflowRun | None:
        """Find a non-terminal run for this contact on this workflow, if any."""
        result = await self.session.execute(
            select(AutomationWorkflowRun)
            .where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.workflow_id == workflow_id,
                AutomationWorkflowRun.contact_id == contact_id,
                AutomationWorkflowRun.status.in_(_ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _find_by_idempotency(
        self,
        institution_id: str,
        workflow_version_id: str,
        idempotency_key: str,
    ) -> AutomationWorkflowRun | None:
        """Look up an existing run by the same grain as the DB unique index
        (institution_id, workflow_version_id, idempotency_key)."""
        result = await self.session.execute(
            select(AutomationWorkflowRun).where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.workflow_version_id == workflow_version_id,
                AutomationWorkflowRun.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def cancel_run(
        self,
        run: AutomationWorkflowRun,
        *,
        reason: str | None = None,
    ) -> AutomationWorkflowRun:
        """Cancel an active or waiting run. No-op for already-terminal runs."""
        if run.status in (
            AutomationRunStatus.COMPLETED.value,
            AutomationRunStatus.CANCELLED.value,
        ):
            return run

        run.status = AutomationRunStatus.CANCELLED.value
        run.cancelled_at = datetime.now(tz=timezone.utc)
        if reason:
            run.blocked_reason = reason
        await self.session.flush()

        self.session.add(
            AutomationWorkflowEvent(
                institution_id=run.institution_id,
                location_id=run.location_id,
                workflow_run_id=run.id,
                event_type="run.cancelled",
                event_metadata={"reason": reason},
            )
        )
        await self.session.flush()
        return run
