"""Clinic-facing connector health for GoTracker locations (Task 13).

When the GoTracker connector on the clinic's PC stops reaching the cloud,
bookings stop syncing and the front desk has no way to know — only operator
alarms fire. This exposes the health signal the GoTracker cloud already holds,
so the dashboard can warn clinic staff while the connection is down and clear
the warning once it recovers.

Read-only: it reads connection state, never writes. NexHealth and no-PMS
clinics have no local connector, so they get ``applicable: false``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.app.api.deps import get_current_institution_or_location_user
from src.app.models.user import User
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.services.sms_privacy import safe_error_summary

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/institution/connector-health", tags=["Connector Health"]
)

#: Connection states a clinic user should be warned about. "unknown" is here on
#: purpose: if we cannot confirm the practice system is healthy, the front desk
#: should be told bookings may not be syncing rather than shown all-clear.
_WARNING_STATES = {"offline", "never_connected", "degraded", "unknown"}

_STATE_MESSAGES: dict[str, str] = {
    "online": "Your practice system connection is active.",
    "degraded": (
        "Your practice system connection is unstable, so some bookings may not sync."
    ),
    "offline": (
        "Your practice system connection is down, so bookings may not sync."
    ),
    "never_connected": (
        "Your practice system has not connected yet, so bookings may not sync."
    ),
    "unknown": (
        "We can't reach your practice system right now, so bookings may not sync."
    ),
}


class ConnectorHealthResponse(BaseModel):
    applicable: bool = False
    #: online | degraded | offline | never_connected | unknown | not_applicable
    status: str = "unknown"
    warning: bool = False
    message: str | None = None
    connector_version: str | None = None
    last_report_at: str | None = None
    seconds_since_last_report: int | None = None
    queue_depth: int | None = None
    failed_write_count: int | None = None
    conflict_count: int | None = None


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _normalize(health: dict[str, Any]) -> ConnectorHealthResponse:
    online = bool(health.get("online"))
    raw_status = health.get("connection_status") or health.get("status")
    status = (
        str(raw_status).strip().lower()
        if raw_status
        else ("online" if online else "unknown")
    )
    if status not in _STATE_MESSAGES or status == "not_applicable":
        status = "online" if online else "unknown"
    return ConnectorHealthResponse(
        applicable=True,
        status=status,
        warning=status in _WARNING_STATES,
        message=_STATE_MESSAGES[status],
        connector_version=_as_str(health.get("connector_version")),
        last_report_at=_as_str(health.get("last_report_at")),
        seconds_since_last_report=_as_int(health.get("seconds_since_last_report")),
        queue_depth=_as_int(health.get("queue_depth")),
        failed_write_count=_as_int(health.get("failed_write_count")),
        conflict_count=_as_int(health.get("conflict_count")),
    )


@router.get("", response_model=ConnectorHealthResponse)
async def get_connector_health(
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    pms: PMSAdapter = Depends(get_institution_pms),
) -> ConnectorHealthResponse:
    """Whether this clinic's practice-system connection is healthy."""
    method = getattr(pms, "get_connector_health", None)
    if getattr(pms, "source", None) != "gotracker" or not callable(method):
        # NexHealth and no-PMS clinics have no local connector to lose.
        return ConnectorHealthResponse(applicable=False, status="not_applicable")

    try:
        health = await method()
    except Exception as exc:  # noqa: BLE001 - a health read must never 500
        logger.warning("connector health read failed: %s", safe_error_summary(exc))
        return ConnectorHealthResponse(
            applicable=True,
            status="unknown",
            warning=True,
            message=_STATE_MESSAGES["unknown"],
        )
    finally:
        try:
            await pms.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the read
            logger.warning("connector health adapter cleanup failed: %s", type(exc).__name__)

    return _normalize(health if isinstance(health, dict) else {})
