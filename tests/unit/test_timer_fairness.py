"""Timer claiming must be fair across tenants and must not cap throughput.

Two coupled faults: claiming exactly 50 timers per 30-second beat gave the whole
platform about 100 steps a minute, and ordering purely by due time meant the
tenant with the biggest backlog took every slot. A single large recall campaign
therefore delayed every other clinic's reminders for as long as it ran.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.app.services.automation.scheduler_service import _round_robin_by_institution

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _timer(institution: str, seconds_overdue: int, ref: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        institution_id=institution,
        due_at=_NOW - timedelta(seconds=seconds_overdue),
        id=f"{institution}-{ref or seconds_overdue}",
    )


def test_short_candidate_list_passes_through_untouched() -> None:
    candidates = [_timer("a", 10), _timer("b", 9)]
    assert _round_robin_by_institution(candidates, 50) == candidates


def test_one_tenants_backlog_cannot_take_every_slot() -> None:
    """The exact starvation case: a bulk enrolment against ordinary traffic."""
    blast = [_timer("busy", 100 - i, ref=f"blast-{i}") for i in range(100)]
    quiet = [_timer("quiet", 5, ref="quiet-0"), _timer("quiet", 4, ref="quiet-1")]

    # Every blast timer is more overdue, so due_at ordering alone would take all 10.
    selected = _round_robin_by_institution(blast + quiet, 10)

    institutions = {timer.institution_id for timer in selected}
    assert institutions == {"busy", "quiet"}
    assert sum(1 for t in selected if t.institution_id == "quiet") == 2


def test_deals_one_per_tenant_per_pass() -> None:
    a = [_timer("a", 30, ref=f"a{i}") for i in range(5)]
    b = [_timer("b", 20, ref=f"b{i}") for i in range(5)]
    c = [_timer("c", 10, ref=f"c{i}") for i in range(5)]

    selected = _round_robin_by_institution(a + b + c, 6)

    # Two full passes: one timer from each tenant, then a second.
    assert [t.institution_id for t in selected] == ["a", "b", "c", "a", "b", "c"]


def test_longest_waiting_tenant_is_dealt_to_first() -> None:
    fresh = [_timer("fresh", 1, ref=f"f{i}") for i in range(5)]
    stale = [_timer("stale", 900, ref=f"s{i}") for i in range(5)]

    selected = _round_robin_by_institution(fresh + stale, 4)

    assert selected[0].institution_id == "stale"
    assert {t.institution_id for t in selected} == {"stale", "fresh"}


def test_a_tenant_with_a_backlog_still_drains_when_alone() -> None:
    """Fairness must not become a per-tenant rate limit."""
    only = [_timer("solo", 100 - i, ref=f"s{i}") for i in range(30)]
    selected = _round_robin_by_institution(only, 10)
    assert len(selected) == 10
    assert {t.institution_id for t in selected} == {"solo"}


def test_selection_never_exceeds_the_limit() -> None:
    candidates = [
        _timer(f"inst-{i % 7}", 50 - i, ref=f"t{i}") for i in range(200)
    ]
    assert len(_round_robin_by_institution(candidates, 13)) == 13


def test_exhausted_queues_do_not_stall_the_deal() -> None:
    """A tenant with one timer must not block others from filling the batch."""
    single = [_timer("small", 60)]
    many = [_timer("large", 50 - i, ref=f"l{i}") for i in range(20)]

    selected = _round_robin_by_institution(single + many, 10)

    assert len(selected) == 10
    assert sum(1 for t in selected if t.institution_id == "small") == 1
