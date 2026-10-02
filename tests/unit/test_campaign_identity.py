"""The identity gate in front of a campaign link.

Most of what is asserted here is what the gate refuses to reveal. The voice
agent answers no-match, several-matches and a wrong date of birth identically,
and that property is the whole reason the gate is not a way to test guesses —
so it has to survive being reached from a public web page too.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.automation.campaign_identity import (
    MAX_ATTEMPTS,
    attempts_used,
    is_locked,
    is_verified,
    mark_verified,
    record_attempt,
    verify_identity,
)


def _patient(first="Dana", last="Reyes", dob="1988-04-02", phone="5054821234", email="dana@example.com", pid="nh-42"):
    return SimpleNamespace(
        id=pid,
        first_name=first,
        last_name=last,
        date_of_birth=dob,
        phone=phone,
        email=email,
    )


def _adapter(results):
    adapter = MagicMock()
    adapter.search_patients = AsyncMock(return_value=results)
    return adapter


def _run():
    return SimpleNamespace(trigger_metadata=None)


GOOD = dict(
    full_name="Dana Reyes",
    date_of_birth="1988-04-02",
    phone="5054821234",
    email=None,
)


@pytest.mark.asyncio
class TestExactlyOneOrNothing:
    async def test_one_match_verifies(self):
        outcome = await verify_identity(_adapter([_patient()]), run=_run(), **GOOD)
        assert outcome.ok
        assert outcome.patient_id == "nh-42"

    async def test_two_matches_are_refused_not_offered(self):
        """Never a picker: showing candidates confirms those people exist."""
        outcome = await verify_identity(
            _adapter([_patient(pid="nh-1"), _patient(pid="nh-2")]), run=_run(), **GOOD
        )
        assert not outcome.ok
        assert outcome.patient_id is None

    async def test_no_match_is_refused(self):
        outcome = await verify_identity(_adapter([]), run=_run(), **GOOD)
        assert not outcome.ok

    async def test_a_wrong_date_of_birth_is_refused(self):
        outcome = await verify_identity(
            _adapter([_patient(dob="1990-01-01")]), run=_run(), **GOOD
        )
        assert not outcome.ok


@pytest.mark.asyncio
class TestTheAnswerIsIndistinguishable:
    async def test_all_three_failures_return_the_same_status(self):
        """The property the whole design rests on."""
        none_found = await verify_identity(_adapter([]), run=_run(), **GOOD)
        ambiguous = await verify_identity(
            _adapter([_patient(pid="a"), _patient(pid="b")]), run=_run(), **GOOD
        )
        wrong_dob = await verify_identity(
            _adapter([_patient(dob="1971-01-01")]), run=_run(), **GOOD
        )
        assert none_found.status == ambiguous.status == wrong_dob.status == "not_matched"

    async def test_a_practice_software_outage_is_not_reported_as_absence(self):
        """"We are down" must not read as "you are not a patient here"."""
        adapter = MagicMock()
        adapter.search_patients = AsyncMock(side_effect=RuntimeError("PMS down"))
        outcome = await verify_identity(adapter, run=_run(), **GOOD)
        assert outcome.status == "not_matched"

    async def test_the_reason_never_travels_with_a_success_or_to_the_page(self):
        """reason is for logs and staff; the route returns only status."""
        outcome = await verify_identity(
            _adapter([_patient(pid="a"), _patient(pid="b")]), run=_run(), **GOOD
        )
        assert outcome.reason == "ambiguous"  # internal only
        assert outcome.status == "not_matched"  # what the patient is told


@pytest.mark.asyncio
class TestAttemptsAreCapped:
    async def test_each_failure_counts(self):
        run = _run()
        await verify_identity(_adapter([]), run=run, **GOOD)
        await verify_identity(_adapter([]), run=run, **GOOD)
        assert attempts_used(run) == 2

    async def test_running_out_locks_the_run(self):
        run = _run()
        for _ in range(MAX_ATTEMPTS):
            await verify_identity(_adapter([]), run=run, **GOOD)
        assert is_locked(run)

    async def test_a_locked_run_does_not_reach_the_practice_software(self):
        """No more queries once the budget is gone, correct details or not."""
        run = _run()
        for _ in range(MAX_ATTEMPTS):
            await verify_identity(_adapter([]), run=run, **GOOD)
        adapter = _adapter([_patient()])
        outcome = await verify_identity(adapter, run=run, **GOOD)
        assert outcome.status == "locked"
        adapter.search_patients.assert_not_awaited()

    async def test_success_clears_the_budget(self):
        """A later action on the same run must not inherit a spent budget."""
        run = _run()
        await verify_identity(_adapter([]), run=run, **GOOD)
        await verify_identity(_adapter([_patient()]), run=run, **GOOD)
        assert attempts_used(run) == 0
        assert is_verified(run)


class TestRunState:
    def test_a_fresh_run_is_neither_verified_nor_locked(self):
        run = _run()
        assert not is_verified(run)
        assert not is_locked(run)

    def test_marking_verified_records_the_patient(self):
        run = _run()
        mark_verified(run, "nh-9")
        assert is_verified(run)
        assert run.trigger_metadata["identity_patient_id"] == "nh-9"

    def test_recording_an_attempt_preserves_other_metadata(self):
        run = SimpleNamespace(trigger_metadata={"booked_start": "2026-09-02T10:00"})
        record_attempt(run)
        assert run.trigger_metadata["booked_start"] == "2026-09-02T10:00"
        assert run.trigger_metadata["identity_attempts"] == 1
