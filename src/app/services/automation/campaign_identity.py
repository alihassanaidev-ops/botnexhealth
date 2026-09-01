"""Identify the person who followed a campaign link, before they act.

A campaign link binds a run, and the run already names a contact — so the
system "knows" who it sent to. That is not the same as knowing who is holding
the phone. A number reaches a household, not a person: the contact model says
so out loud ("a single phone number may link to multiple Contact records"), and
a number given to a clinic 18 months ago may since have been reassigned.

The voice agent solves the same problem with ``lookup_patient``: name plus date
of birth plus an exact phone or email, and — the part that matters — the *same
neutral answer* for no match, several matches, and a mismatched claim. This is
that gate, reached from a page instead of a call, and it deliberately reuses
``_identity_gate_passes`` rather than growing a second, subtly different
matcher.

Three rules carried over, each for a reason:

* **Exactly one, or nothing.** Never a list of candidates to choose from —
  showing "did you mean these two?" confirms those people exist at this clinic
  to someone who has proved nothing.
* **One message for every failure.** Different wording per failure mode turns
  the form into an oracle.
* **Attempts are capped.** A phone call has natural friction; a web form has
  none, and a date of birth is a few tens of thousands of guesses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Attempts allowed per run before the flow stops asking and fetches a human.
#: Low on purpose: a patient entering their own details needs one or two goes,
#: and anyone needing more is either stuck (and better served by staff) or
#: guessing.
MAX_ATTEMPTS = 5

#: Where the attempt count lives on the run.
ATTEMPTS_KEY = "identity_attempts"
#: Set once the run's contact has been confirmed to be the person present.
VERIFIED_KEY = "identity_verified"


@dataclass(frozen=True)
class IdentityOutcome:
    """What the gate decided.

    ``status`` is the only thing the page is told. ``reason`` is for logs and
    the staff handoff — never for the patient, because the difference between
    "no such person" and "that is not their date of birth" is exactly what must
    not travel back over the wire.
    """

    status: str  # "verified" | "not_matched" | "locked"
    patient_id: str | None = None
    reason: str | None = None
    attempts_remaining: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "verified"


def attempts_used(run: Any) -> int:
    metadata = getattr(run, "trigger_metadata", None) or {}
    value = metadata.get(ATTEMPTS_KEY)
    return value if isinstance(value, int) and value > 0 else 0


def is_locked(run: Any, *, max_attempts: int = MAX_ATTEMPTS) -> bool:
    return attempts_used(run) >= max_attempts


def is_verified(run: Any) -> bool:
    metadata = getattr(run, "trigger_metadata", None) or {}
    return bool(metadata.get(VERIFIED_KEY))


def record_attempt(run: Any) -> int:
    """Count a failed attempt on the run. Returns the new total."""
    metadata = dict(getattr(run, "trigger_metadata", None) or {})
    used = attempts_used(run) + 1
    metadata[ATTEMPTS_KEY] = used
    run.trigger_metadata = metadata
    return used


def mark_verified(run: Any, patient_id: str) -> None:
    """Record that the person present proved they are this patient.

    Also resets the attempt count: the run is past the gate, and a later
    action on the same run should not inherit a near-exhausted budget.
    """
    metadata = dict(getattr(run, "trigger_metadata", None) or {})
    metadata[VERIFIED_KEY] = True
    metadata["identity_patient_id"] = str(patient_id)
    metadata[ATTEMPTS_KEY] = 0
    run.trigger_metadata = metadata


async def verify_identity(
    adapter: Any,
    *,
    full_name: str,
    date_of_birth: str,
    phone: str | None,
    email: str | None,
    max_attempts: int = MAX_ATTEMPTS,
    run: Any = None,
) -> IdentityOutcome:
    """Resolve the supplied details to exactly one patient, or to nothing.

    ``email`` narrows and never widens: it is added to the same search rather
    than searched for separately, so supplying a mistyped address cannot turn a
    single match into none by racing a second query against the first.
    """
    if run is not None and is_locked(run, max_attempts=max_attempts):
        return IdentityOutcome(status="locked", reason="attempts_exhausted")

    # Key names match what ``_identity_gate_passes`` reads, so the two callers
    # stay in step: "name", not "patient_name".
    args: dict[str, Any] = {
        "name": full_name,
        "date_of_birth": date_of_birth,
    }
    if phone:
        args["phone_number"] = phone
    if email:
        args["email"] = email

    from src.app.retell.handlers import _identity_gate_passes

    try:
        patients = await adapter.search_patients(
            full_name,
            name=full_name,
            email=email,
            phone_number=phone,
            date_of_birth=date_of_birth,
            include=None,
        )
    except Exception:
        # Never distinguishable from "not matched" by the caller: a practice
        # software outage must not read as "that person does not exist".
        logger.exception("identity search failed")
        return IdentityOutcome(status="not_matched", reason="search_failed")

    verified: list[Any] = []
    reasons: list[str] = []
    for patient in patients or []:
        passed, reason = _identity_gate_passes(patient, args)
        if passed:
            verified.append(patient)
        elif reason:
            reasons.append(reason)

    if len(verified) != 1:
        # One branch for no match, several matches, and a wrong claim.
        reason = (
            "ambiguous" if len(verified) > 1
            else (",".join(sorted(set(reasons))) or "no_match")
        )
        logger.info(
            "campaign identity denied: candidates=%d verified=%d reason=%s",
            len(patients or []),
            len(verified),
            reason,
        )
        used = record_attempt(run) if run is not None else 0
        remaining = max(0, max_attempts - used)
        return IdentityOutcome(
            status="locked" if remaining == 0 else "not_matched",
            reason=reason,
            attempts_remaining=remaining,
        )

    patient_id = str(getattr(verified[0], "id", "") or "")
    if not patient_id:
        return IdentityOutcome(status="not_matched", reason="no_patient_id")
    if run is not None:
        mark_verified(run, patient_id)
    return IdentityOutcome(status="verified", patient_id=patient_id)
