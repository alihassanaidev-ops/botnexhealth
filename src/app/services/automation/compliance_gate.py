"""Compliance gate contract between Plan 01 (workflow engine) and Plan 12 (compliance).

The dispatcher calls gate.check() before firing any send node. A real
ComplianceGate is implemented against Plan 12; this module ships the protocol and
a no-op stub so the engine runs safely without a compliance layer in place.

Hold semantics: a "hold" defers the send until the next permitted send window.
The gate sets ``GateResult.retry_at`` to that window; the dispatcher schedules a
timer for that time and the run resumes and re-evaluates the gate then (it is
never dropped). If no permitted window exists within the horizon the gate returns
"block" instead of "hold".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.app.models.automation_workflow import AutomationWorkflowRun


@dataclass
class GateResult:
    action: Literal["allow", "block", "hold"]
    reason: str | None = None
    # For action == "hold": the UTC time the send should be retried (next
    # permitted send window). None for allow/block.
    retry_at: datetime | None = None


@runtime_checkable
class ComplianceGate(Protocol):
    """Check whether a workflow run may send on a given channel.

    Args:
        run: The active workflow run.
        channel_type: One of "send_sms", "send_voice", "send_email".

    Returns:
        GateResult with action "allow", "block", or "hold".
    """

    async def check(self, run: "AutomationWorkflowRun", channel_type: str) -> GateResult: ...


class NoOpComplianceGate:
    """Default stub — always allows. Replaced by Plan 12 implementation."""

    async def check(self, run: "AutomationWorkflowRun", channel_type: str) -> GateResult:
        return GateResult(action="allow")
