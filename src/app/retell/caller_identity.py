"""Which patients the caller on a voice call has proven to be.

Retell function calls are separate requests, so ``lookup_patient`` passing the
identity gate leaves no trace a later ``cancel_appointment`` can see. Without
one, the agent could cancel, confirm or reschedule any appointment id it was
given. ``lookup_patient`` records each verified patient against the call here,
and the appointment-changing handlers check the appointment's patient against
that record before acting.

Rows live in ``retell_function_invocations`` under their own function name, so
they share that table's per-call scoping and retention. The patient id is kept
only as a keyed hash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.database import get_system_db_session
from src.app.security import keyed_hash

logger = logging.getLogger(__name__)

VERIFIED_PATIENT_FUNCTION = "verified_patient"


def raw_pms_id(value: object) -> str:
    """An id without the platform's ``nh-``/``gt-`` prefix."""
    text = str(value or "").strip()
    for prefix in ("nh-", "gt-"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _patient_marker(patient_id: object) -> str:
    return keyed_hash(raw_pms_id(patient_id), purpose="retell_verified_patient")


async def remember_verified_patient(
    call_id: str | None, patient_id: object, institution_id: str | None
) -> None:
    """Record that this call's caller passed the identity gate as this patient.

    Never raises: a lookup must not fail because the record could not be
    written. The caller is then simply refused a later appointment change.
    """
    if not call_id or not raw_pms_id(patient_id):
        return
    from src.app.models.retell_function_invocation import (
        RetellFunctionInvocation,
        RetellFunctionStatus,
    )

    now = datetime.now(timezone.utc)
    try:
        async with get_system_db_session(
            "retell_function", external_id=call_id
        ) as session:
            session.add(
                RetellFunctionInvocation(
                    call_id=call_id,
                    function_name=VERIFIED_PATIENT_FUNCTION,
                    args_hash=_patient_marker(patient_id),
                    status=RetellFunctionStatus.COMPLETED.value,
                    attempts=1,
                    institution_id=institution_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # Already verified earlier in this call.
                await session.rollback()
    except Exception:  # noqa: BLE001
        logger.exception("could not record verified patient for call")


async def call_verified_patient(call_id: str | None, patient_id: object) -> bool:
    """Whether this call's caller was verified as this patient."""
    if not call_id or not raw_pms_id(patient_id):
        return False
    from src.app.models.retell_function_invocation import RetellFunctionInvocation

    async with get_system_db_session("retell_function", external_id=call_id) as session:
        row = (
            await session.execute(
                select(RetellFunctionInvocation.id)
                .where(
                    RetellFunctionInvocation.call_id == call_id,
                    RetellFunctionInvocation.function_name
                    == VERIFIED_PATIENT_FUNCTION,
                    RetellFunctionInvocation.args_hash == _patient_marker(patient_id),
                )
                .limit(1)
            )
        ).first()
    return row is not None


async def outbound_call_appointment_id(
    call_id: str | None, institution_id: str | None, location_id: str | None
) -> str | None:
    """The appointment an outbound campaign call was placed about, if any.

    A confirmation call is dialled for one appointment, so acting on that
    appointment needs no separate lookup.
    """
    if not call_id or not institution_id:
        return None
    from src.app.models.automation_workflow import AutomationWorkflowRun
    from src.app.models.outbound_voice import WorkflowVoiceAttempt

    async with get_system_db_session(
        "retell",
        institution_id=str(institution_id),
        location_id=str(location_id) if location_id else None,
    ) as session:
        run_id = (
            await session.execute(
                select(WorkflowVoiceAttempt.workflow_run_id)
                .where(WorkflowVoiceAttempt.retell_call_id == str(call_id))
                .limit(1)
            )
        ).scalar_one_or_none()
        if run_id is None:
            return None
        run = await session.get(AutomationWorkflowRun, run_id)
    if run is None or run.trigger_ref_type != "appointment" or not run.trigger_ref_id:
        return None
    return str(run.trigger_ref_id)
