"""Compliance gate contract between Plan 01 (workflow engine) and Plan 12 (compliance).

The dispatcher calls gate.check() before firing any send node. Dev B implements
a real ComplianceGate against Plan 12; this module ships the protocol and a
no-op stub so the engine runs safely without a compliance layer in place.

Hold semantics (this slice): hold terminates the run with outcome
"compliance_hold" rather than re-queuing. Re-queue support (consent-pending
flows) is deferred to a later slice when the consent flow is defined.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.app.models.automation_workflow import AutomationWorkflowRun


@dataclass
class GateResult:
    action: Literal["allow", "block", "hold"]
    reason: str | None = None


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
