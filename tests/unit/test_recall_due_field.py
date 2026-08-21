"""The recall scanner must read NexHealth's actual due-date field.

NexHealth returns `date_due`. The scanner read `due_date`/`due`/
`next_visit_date`, none of which exist, so every record fell through to the
"missing due date is treated as due" branch. Combined with the endpoint fix
(/recalls -> /patient_recalls, which had been 404ing) that would have marked
all 8,862 recalls at one clinic as overdue — including ones due in 2029 —
and enqueued an outbound workflow for each.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.app.tasks.automation_workflow import _recall_is_due

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def test_past_date_due_is_due():
    assert _recall_is_due({"date_due": "2023-05-06"}, now=NOW) is True


def test_future_date_due_is_not_due():
    """The regression: this returned True and messaged patients years early."""
    assert _recall_is_due({"date_due": "2029-05-01"}, now=NOW) is False


def test_date_due_today_is_due():
    assert _recall_is_due({"date_due": "2026-08-21"}, now=NOW) is True


def test_legacy_spellings_still_honoured():
    """Kept as fallbacks so a differently-shaped PMS payload still works."""
    assert _recall_is_due({"due_date": "2029-05-01"}, now=NOW) is False
    assert _recall_is_due({"due": "2029-05-01"}, now=NOW) is False


def test_genuinely_missing_date_is_still_treated_as_due():
    """Deliberate: a record on the recall queue with no date is actionable."""
    assert _recall_is_due({}, now=NOW) is True


def test_unparseable_date_is_treated_as_due():
    assert _recall_is_due({"date_due": "not-a-date"}, now=NOW) is True
