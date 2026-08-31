"""Quiet-hours exceptions (Item 20).

Two behaviours are load-bearing and are asserted here alongside the new ones,
because an exception mechanism is exactly the change that would break them: a
send due inside quiet hours is **held until the window opens**, never dropped
and never sent early; and a location with no permitted window is **blocked**
rather than let through.

The evaluator is exercised against stubbed rows rather than a database. What is
under test is the precedence rule and the window arithmetic, and both are pure
given the rows — a real database would slow the suite without testing more of
them. RLS on the table has its own coverage in the isolation suite.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.quiet_hours_exception import QuietHoursException
from src.app.services.automation.quiet_hours_exception_service import (
    QuietHoursExceptionError,
    QuietHoursExceptionService,
)
from src.app.services.automation.quiet_hours_service import QuietHoursService

LOCATION = "loc-1"
UTC = timezone.utc


def _hours(day_of_week: int, open_h: int = 9, close_h: int = 17, is_open: bool = True):
    return SimpleNamespace(
        day_of_week=day_of_week,
        is_open=is_open,
        open_time=time(open_h, 0),
        close_time=time(close_h, 0),
    )


def _exception(**kwargs) -> QuietHoursException:
    row = QuietHoursException(
        institution_id="inst-1",
        location_id=LOCATION,
        contact_id=kwargs.get("contact_id"),
        exception_date=kwargs.get("exception_date"),
        content_class=kwargs.get("content_class"),
        is_blocked=kwargs.get("is_blocked", False),
        open_time=kwargs.get("open_time"),
        close_time=kwargs.get("close_time"),
    )
    row.created_at = kwargs.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    return row


def _service(*, exceptions=None, weekly=None, timezone_name="UTC") -> QuietHoursService:
    """A QuietHoursService over stubbed rows."""
    exceptions = exceptions or []
    weekly = weekly if weekly is not None else [_hours(d) for d in range(7)]

    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=LOCATION, timezone=timezone_name)
    )

    async def _execute(stmt):
        text = str(stmt)
        result = MagicMock()
        if "quiet_hours_exceptions" in text:
            scalars = MagicMock()
            scalars.all = MagicMock(return_value=exceptions)
            result.scalars = MagicMock(return_value=scalars)
            return result
        # LocationOperatingHours lookup — the compiled SQL carries the day as a
        # bind parameter, so the stub answers with whichever row the caller's
        # day matches, found by re-reading the parameter.
        day = stmt.compile().params.get("day_of_week_1")
        match = next((h for h in weekly if h.day_of_week == day), None)
        result.scalar_one_or_none = MagicMock(return_value=match)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return QuietHoursService(session)


# ── The behaviours that must not regress ─────────────────────────────


def test_a_send_inside_quiet_hours_is_still_quiet() -> None:
    svc = _service()
    at_3am = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)  # a Tuesday
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_3am)) is True


def test_a_send_inside_the_window_is_permitted() -> None:
    svc = _service()
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon)) is False


def test_work_held_overnight_resumes_when_the_window_opens() -> None:
    """Held until morning, never sent early — the rule the gate depends on."""
    svc = _service()
    at_3am = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    resume = asyncio.run(svc.next_permitted_window(LOCATION, now=at_3am))
    assert resume == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def test_a_location_closed_every_day_has_no_window() -> None:
    """None is what the compliance gate turns into a block, not a hold."""
    svc = _service(weekly=[_hours(d, is_open=False) for d in range(7)])
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.next_permitted_window(LOCATION, now=now)) is None


def test_an_unconfigured_location_is_unrestricted() -> None:
    svc = _service(weekly=[])
    at_3am = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_3am)) is False


# ── Date exceptions ──────────────────────────────────────────────────


def test_a_date_exception_prevents_contact_on_that_date() -> None:
    """The public-holiday case: open by the weekly hours, shut in reality."""
    holiday = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    svc = _service(exceptions=[holiday])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon)) is True


def test_a_date_exception_does_not_leak_into_other_dates() -> None:
    holiday = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    svc = _service(exceptions=[holiday])
    next_day = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=next_day)) is False


def test_work_on_a_blocked_date_is_held_until_the_next_open_day() -> None:
    holiday = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    svc = _service(exceptions=[holiday])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    resume = asyncio.run(svc.next_permitted_window(LOCATION, now=at_noon))
    assert resume == datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


# ── Patient exceptions ───────────────────────────────────────────────


def test_a_patient_exception_narrows_that_patients_window() -> None:
    """Someone who asked to be contacted only in the evenings."""
    evenings = _exception(
        contact_id="c-1", open_time=time(18, 0), close_time=time(20, 0)
    )
    svc = _service(exceptions=[evenings])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon, contact_id="c-1"))
    assert not asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon))


def test_a_patient_exception_does_not_affect_other_patients() -> None:
    evenings = _exception(
        contact_id="c-1", open_time=time(18, 0), close_time=time(20, 0)
    )
    svc = _service(exceptions=[evenings])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert not asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon, contact_id="c-2"))


# ── Content-class exceptions ─────────────────────────────────────────


def test_a_content_class_exception_narrows_only_that_class() -> None:
    marketing = _exception(
        content_class="marketing", open_time=time(10, 0), close_time=time(16, 0)
    )
    svc = _service(exceptions=[marketing])
    at_9_30 = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)

    assert asyncio.run(
        svc.is_quiet_hours(LOCATION, now=at_9_30, content_class="marketing")
    )
    assert not asyncio.run(
        svc.is_quiet_hours(LOCATION, now=at_9_30, content_class="transactional_care")
    )


def test_an_exception_can_widen_a_window_not_only_narrow_it() -> None:
    """A reminder for a 7am appointment must be allowed out before opening."""
    early = _exception(
        content_class="transactional_care",
        open_time=time(6, 0),
        close_time=time(20, 0),
    )
    svc = _service(exceptions=[early])
    at_6_30 = datetime(2026, 9, 1, 6, 30, tzinfo=UTC)

    assert not asyncio.run(
        svc.is_quiet_hours(LOCATION, now=at_6_30, content_class="transactional_care")
    )
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_6_30))


# ── Precedence ───────────────────────────────────────────────────────


def test_a_patient_rule_beats_a_clinic_rule() -> None:
    clinic = _exception(open_time=time(9, 0), close_time=time(17, 0))
    patient = _exception(
        contact_id="c-1", open_time=time(18, 0), close_time=time(20, 0)
    )
    svc = _service(exceptions=[clinic, patient])
    at_7pm = datetime(2026, 9, 1, 19, 0, tzinfo=UTC)
    assert not asyncio.run(svc.is_quiet_hours(LOCATION, now=at_7pm, contact_id="c-1"))


def test_a_patient_rule_beats_a_dated_clinic_rule() -> None:
    """Weighted so a patient's own preference always wins, whatever else matches."""
    dated_clinic = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    patient = _exception(
        contact_id="c-1", open_time=time(18, 0), close_time=time(20, 0)
    )
    svc = _service(exceptions=[dated_clinic, patient])
    at_7pm = datetime(2026, 9, 1, 19, 0, tzinfo=UTC)
    assert not asyncio.run(svc.is_quiet_hours(LOCATION, now=at_7pm, contact_id="c-1"))


def test_a_dated_rule_beats_an_undated_one() -> None:
    undated = _exception(open_time=time(9, 0), close_time=time(17, 0))
    dated = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    svc = _service(exceptions=[undated, dated])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon))


def test_the_newest_of_two_equally_specific_rules_wins() -> None:
    older = _exception(
        open_time=time(9, 0),
        close_time=time(10, 0),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = _exception(
        open_time=time(9, 0),
        close_time=time(17, 0),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    svc = _service(exceptions=[older, newer])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert not asyncio.run(svc.is_quiet_hours(LOCATION, now=at_noon))


# ── Save-time validation ─────────────────────────────────────────────


def _validation_service(window_result):
    """A service whose evaluator reports *window_result* for any query."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = QuietHoursExceptionService(session)
    svc._check_leaves_a_window = AsyncMock(
        side_effect=None if window_result else _raise_no_window
    )
    return svc


async def _raise_no_window(*args, **kwargs):
    raise QuietHoursExceptionError("no window")


def test_an_inverted_window_is_rejected_at_save_time() -> None:
    """Caught before the database, with an explanation an operator can act on."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = QuietHoursExceptionService(session)

    with pytest.raises(QuietHoursExceptionError) as exc:
        asyncio.run(
            svc.create(
                institution_id="inst-1",
                location_id=LOCATION,
                open_time=time(18, 0),
                close_time=time(9, 0),
            )
        )

    assert "permits no time at all" in str(exc.value)
    session.add.assert_not_called()


def test_an_exception_leaving_no_window_is_rejected_with_an_explanation() -> None:
    """The dangerous one: every send would hold for a window that never opens."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = QuietHoursExceptionService(session)
    svc._check_leaves_a_window = _raise_no_window

    with pytest.raises(QuietHoursExceptionError):
        asyncio.run(
            svc.create(
                institution_id="inst-1", location_id=LOCATION, is_blocked=True
            )
        )


def test_a_workable_exception_is_accepted() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = QuietHoursExceptionService(session)
    svc._check_leaves_a_window = AsyncMock()

    row = asyncio.run(
        svc.create(
            institution_id="inst-1",
            location_id=LOCATION,
            exception_date=date(2026, 12, 25),
            is_blocked=True,
            reason="Christmas Day",
        )
    )

    assert row.is_blocked is True
    assert row.exception_date == date(2026, 12, 25)
    session.add.assert_called_once()


def test_a_full_block_is_allowed_when_it_only_covers_one_date() -> None:
    """Blocking Christmas is fine; blocking every day is not."""
    holiday = _exception(exception_date=date(2026, 9, 1), is_blocked=True)
    svc = _service(exceptions=[holiday])
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert asyncio.run(svc.next_permitted_window(LOCATION, now=at_noon)) is not None


def test_specificity_ranks_patient_above_date_above_content_class() -> None:
    assert _exception(contact_id="c-1").specificity > _exception(
        exception_date=date(2026, 9, 1), content_class="marketing"
    ).specificity
    assert (
        _exception(exception_date=date(2026, 9, 1)).specificity
        > _exception(content_class="marketing").specificity
    )
    assert _exception().specificity == 0


# ── Dial counting for Item 19 ────────────────────────────────────────
# Lives here rather than in the voice suite because it is pure counting logic:
# what counts as a dial, and what counts as an attempt.


def test_voicemail_counts_as_an_attempt_when_configured_to() -> None:
    from src.app.services.automation.voice_attempt_recorder import count_dials_for_node

    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[("voicemail",), ("no_answer",)])
    session.execute = AsyncMock(return_value=result)

    dials, counted = asyncio.run(
        count_dials_for_node(
            session,
            workflow_run_id="run-1",
            step_id="node-1",
            voicemail_consumes_attempt=True,
        )
    )
    assert (dials, counted) == (2, 2)


def test_voicemail_is_excluded_from_attempts_when_configured_not_to() -> None:
    from src.app.services.automation.voice_attempt_recorder import count_dials_for_node

    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(
        return_value=[("voicemail",), ("voicemail",), ("no_answer",)]
    )
    session.execute = AsyncMock(return_value=result)

    dials, counted = asyncio.run(
        count_dials_for_node(
            session,
            workflow_run_id="run-1",
            step_id="node-1",
            voicemail_consumes_attempt=False,
        )
    )
    # Three dials happened; only the no-answer used up an attempt.
    assert (dials, counted) == (3, 1)


def test_an_attempt_still_awaiting_its_outcome_is_already_spent() -> None:
    """A call in flight has been made, whatever its result turns out to be."""
    from src.app.services.automation.voice_attempt_recorder import count_dials_for_node

    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[(None,)])
    session.execute = AsyncMock(return_value=result)

    dials, counted = asyncio.run(
        count_dials_for_node(
            session,
            workflow_run_id="run-1",
            step_id="node-1",
            voicemail_consumes_attempt=False,
        )
    )
    assert (dials, counted) == (1, 1)


def test_a_dial_cap_below_the_allowance_is_rejected() -> None:
    """A cap under the allowance silently shortens the ladder."""
    from pydantic import ValidationError

    from src.app.services.automation.definition_schema import SendVoiceNode

    with pytest.raises(ValidationError) as exc:
        SendVoiceNode(
            id="v1",
            next_node_id="n2",
            retell_agent_id="agent_a",
            voice_attempt_allowance=5,
            max_dials=2,
        )
    assert "max_dials" in str(exc.value)


def test_a_dial_cap_at_or_above_the_allowance_is_accepted() -> None:
    from src.app.services.automation.definition_schema import SendVoiceNode

    node = SendVoiceNode(
        id="v1",
        next_node_id="n2",
        retell_agent_id="agent_a",
        voice_attempt_allowance=3,
        max_dials=3,
    )
    assert node.max_dials == 3


def test_voicemail_defaults_preserve_todays_behaviour() -> None:
    """No message left, voicemail counts — what the engine does now."""
    from src.app.services.automation.definition_schema import SendVoiceNode

    node = SendVoiceNode(id="v1", next_node_id="n2", retell_agent_id="agent_a")
    assert node.leave_voicemail is False
    assert node.voicemail_consumes_attempt is True
