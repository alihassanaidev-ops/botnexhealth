"""What caused a write into a practice's records (Item 34).

When the platform changes something in a clinic's practice software, the
practice may reasonably ask why. Today the honest answer is that somebody would
have to read the source and infer it. There is a campaign run id on the queued
row, and nothing else — no record of *who or what* decided, and nothing that
ties the write to the interaction that produced it.

That is survivable while writes are rare. It stopped being survivable when
GoTracker clinics went live: a duplicate booking is now a real patient's real
time, and "we think a campaign did it" is not an investigation.

This module carries four facts alongside every write:

  ``workflow_run_id`` / ``step_id``
      Which campaign run, and which step of it. Already recorded for campaign
      writes; carried here so every path records it the same way.

  ``actor``
      What kind of thing decided. A campaign step, a patient clicking a link, a
      voice agent mid-call and a member of staff are four different answers to
      "why did this happen", and the run id alone cannot tell them apart — a
      patient booking through a campaign link has a run id too.

  ``trace_id``
      The identifier already following the interaction through the logs. It is
      what lets an operator move from "this booking looks wrong" to the
      conversation that caused it, and it is the piece that has to survive
      across the boundary into the Cloud Service's own record.

The trace id is read from structlog's context rather than threaded through
every call: the request middleware binds it, and Celery tasks bind their own.
Reading it where the write happens keeps every call site from having to
remember, which is how the run id came to be set in some paths and not others.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

import structlog

logger = logging.getLogger(__name__)

__all__ = ["WriteActor", "WriteProvenance", "current_trace_id"]


class WriteActor(str, Enum):
    """What kind of thing decided this write should happen.

    Values are stored and appear in operator-facing views, so they are part of
    the contract. Renaming one orphans the history that used the old name.
    """

    #: A campaign step ran and decided on its own.
    CAMPAIGN = "campaign"
    #: A patient acted on a link we sent them (Item 12).
    PATIENT_LINK = "patient_link"
    #: The voice agent acted during a call.
    VOICE_AGENT = "voice_agent"
    #: A member of clinic staff did it through the dashboard.
    STAFF = "staff"
    #: An operator or a maintenance job. Includes a deliberate replay, which is
    #: worth telling apart from the original attempt.
    SYSTEM = "system"


def current_trace_id() -> str:
    """The identifier following the current interaction, or a fresh one.

    Never returns empty. A write with no trace is the case this item exists to
    prevent, and an absent id would silently reintroduce it — better a trace
    that starts here than a row that cannot be followed at all.
    """
    bound = structlog.contextvars.get_contextvars()
    trace_id = bound.get("request_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    # No bound context: a worker that never bound one, or a call outside a
    # request. Minting one still ties this write to whatever else happens under
    # it, which is more than the nothing recorded before.
    return str(uuid4())


@dataclass(frozen=True)
class WriteProvenance:
    """Why a write happened, travelling with the write itself."""

    actor: WriteActor
    trace_id: str
    workflow_run_id: str | None = None
    step_id: str | None = None
    #: Free text for the cases the four fields above cannot express — an
    #: operator's stated reason for overriding a conflict, most usefully.
    reason: str | None = None

    @classmethod
    def for_campaign(
        cls,
        *,
        workflow_run_id: str | None,
        step_id: str | None,
        trace_id: str | None = None,
    ) -> "WriteProvenance":
        return cls(
            actor=WriteActor.CAMPAIGN,
            trace_id=trace_id or current_trace_id(),
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )

    @classmethod
    def for_patient_link(
        cls,
        *,
        workflow_run_id: str | None,
        step_id: str | None = None,
        trace_id: str | None = None,
    ) -> "WriteProvenance":
        """A patient acting on a link.

        Distinct from ``for_campaign`` even though both carry a run id — the
        campaign sent the link, the patient chose the slot, and an investigation
        into an unexpected booking needs to know which of those to look at.
        """
        return cls(
            actor=WriteActor.PATIENT_LINK,
            trace_id=trace_id or current_trace_id(),
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )

    @classmethod
    def for_system(
        cls, *, reason: str | None = None, trace_id: str | None = None
    ) -> "WriteProvenance":
        return cls(
            actor=WriteActor.SYSTEM,
            trace_id=trace_id or current_trace_id(),
            reason=reason,
        )

    def as_payload(self) -> dict[str, str]:
        """The shape sent across the boundary to the Cloud Service.

        Deliberately flat and string-valued: it is written into another team's
        record of the write, and a nested or typed structure is one more thing
        for the two sides to disagree about. Empty fields are omitted rather
        than sent as null, so the receiving end can treat presence as meaning.
        """
        payload = {"actor": self.actor.value, "trace_id": self.trace_id}
        if self.workflow_run_id:
            payload["workflow_run_id"] = self.workflow_run_id
        if self.step_id:
            payload["step_id"] = self.step_id
        if self.reason:
            payload["reason"] = self.reason
        return payload
