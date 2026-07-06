"""Spoken opt-out → voice suppression (Plan 03 / V-2).

Two halves, split per the A-8 do-not-guess rule:

* **Detection (gated):** `detect_voice_optout` reads a **configured** key from a
  call's post-call `custom_analysis_data`. It is OFF until `retell_optout_analysis_key`
  is set — we never infer a compliance trigger from an unconfirmed field.
* **Write + wiring (built now):** `suppress_voice_optout` records a location-scoped
  `DoNotContact` (blocks all channels for that location — the owner decision), so
  every subsequent dispatch is blocked by the existing `ComplianceGateService`.
"""

from __future__ import annotations

import logging

from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.sms_consent import ConsentSource, DncScope
from src.app.services.sms_compliance import SmsComplianceService

logger = logging.getLogger(__name__)

# Short, non-PHI reason code stamped on the DNC row.
_OPTOUT_REASON = "voice_spoken_optout"

_TRUTHY = frozenset({"true", "yes", "1", "y", "optout", "opt_out", "opted_out", "dnc"})


def detect_voice_optout(custom_analysis: dict | None) -> bool:
    """Whether this call's analysis signals a spoken opt-out.

    Returns False unless `retell_optout_analysis_key` is configured (do-not-guess):
    detection stays disabled until the real Retell field name is confirmed.
    """
    key = settings.retell_optout_analysis_key
    if not key:
        return False
    value = (custom_analysis or {}).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


async def suppress_voice_optout(
    *,
    institution_id: str,
    location_id: str | None,
    contact_id: str | None,
    phone: str | None,
    call_id: str | None = None,
) -> bool:
    """Write a location-scoped DoNotContact for a spoken opt-out. Best-effort.

    Opens its own RLS-scoped session (the webhook's post-call session is closed by
    this point). Never raises — a suppression-write hiccup must not break webhook
    processing; it is logged for follow-up.
    """
    if not phone:
        logger.warning(
            "voice opt-out: no phone to suppress institution=%s call=%s", institution_id, call_id
        )
        return False
    try:
        async with get_system_db_session(
            "retell", institution_id=institution_id, location_id=location_id
        ) as session:
            await SmsComplianceService(session).set_do_not_contact(
                institution_id=institution_id,
                phone=phone,
                scope=DncScope.LOCATION,
                location_id=location_id,
                contact_id=contact_id,
                source=ConsentSource.SYSTEM,
                reason=_OPTOUT_REASON,
            )
            await session.commit()
        logger.info(
            "voice opt-out: location DNC written institution=%s location=%s call=%s",
            institution_id, location_id, call_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the webhook
        logger.error(
            "voice opt-out: failed to write DNC institution=%s call=%s error=%s",
            institution_id, call_id, exc,
        )
        return False
