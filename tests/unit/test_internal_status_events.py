"""The listener that turns a tracked status change into a campaign trigger.

These exercise the decision logic — what counts as a transition, what the event
row records — without a database. The SQLAlchemy wiring around it is thin; the
judgement about *which* writes are worth a campaign is what has to be right.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.services.automation import internal_status_events as ise


class _FakeHistory:
    def __init__(self, added: list | None, deleted: list | None) -> None:
        self.added = added or []
        self.deleted = deleted or []

    def has_changes(self) -> bool:
        return bool(self.added or self.deleted)


class _FakeSession:
    """Just enough of a Session for the collector: `dirty`, `add_all`, `info`."""

    def __init__(self, dirty: list) -> None:
        self.dirty = dirty
        self.added: list = []
        self.info: dict = {}

    def add_all(self, rows) -> None:
        self.added.extend(rows)


def _patch_history(monkeypatch, histories: dict[int, dict[str, _FakeHistory]]) -> None:
    """Stand in for `sqlalchemy.inspect`, keyed by instance identity.

    Mirrors the real shape the collector walks: `inspect(obj).attrs[name].history`.
    """

    def fake_inspect(instance):
        attrs = {
            name: SimpleNamespace(history=history)
            for name, history in histories[id(instance)].items()
        }
        return SimpleNamespace(attrs=attrs)

    monkeypatch.setattr(ise, "inspect", fake_inspect)


class Contact:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class Call:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class CampaignStaffHandoff:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def test_a_lead_status_transition_emits_one_event(monkeypatch) -> None:
    contact = Contact(
        id="c-1", institution_id="inst-1", location_id="loc-1", lead_status="engaged"
    )
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory(["engaged"], ["new"])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert len(session.added) == 1
    event = session.added[0]
    assert event.field == "contact_lead_status"
    assert event.from_status == "new"
    assert event.to_status == "engaged"
    assert event.subject_type == "contact"
    assert event.subject_id == "c-1"
    # A contact IS the contact, so the event is attributable without a join.
    assert event.contact_id == "c-1"
    assert event.institution_id == "inst-1"
    assert event.location_id == "loc-1"


def test_rewriting_the_same_value_is_not_a_transition(monkeypatch) -> None:
    """SQLAlchemy reports a no-op assignment as a change; a campaign must not."""
    contact = Contact(id="c-1", institution_id="inst-1", lead_status="engaged")
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory(["engaged"], ["engaged"])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert session.added == []


def test_clearing_a_status_emits_nothing(monkeypatch) -> None:
    """There is no campaign that starts on "no longer has a status"."""
    contact = Contact(id="c-1", institution_id="inst-1", lead_status=None)
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory([None], ["engaged"])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert session.added == []


def test_first_ever_status_is_a_transition_from_nothing(monkeypatch) -> None:
    contact = Contact(id="c-2", institution_id="inst-1", lead_status="new")
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory(["new"], [])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert len(session.added) == 1
    assert session.added[0].from_status is None
    assert session.added[0].to_status == "new"


def test_call_workflow_status_records_the_label_not_the_id(monkeypatch) -> None:
    """Authors pick "Completed" from the clinic's vocabulary, never a UUID."""
    call = Call(
        id="call-9",
        institution_id="inst-1",
        location_id="loc-2",
        contact_id="c-3",
        workflow_status_id="ws-uuid-2",
        workflow_status=SimpleNamespace(name="Completed"),
    )
    _patch_history(
        monkeypatch,
        {id(call): {"workflow_status_id": _FakeHistory(["ws-uuid-2"], ["ws-uuid-1"])}},
    )
    session = _FakeSession([call])

    ise._collect(session)

    event = session.added[0]
    assert event.field == "call_workflow_status"
    assert event.to_status == "Completed"
    assert event.subject_type == "call"
    assert event.subject_id == "call-9"
    # A call's contact is a different record, so it is carried across.
    assert event.contact_id == "c-3"


def test_handoff_status_is_tracked(monkeypatch) -> None:
    handoff = CampaignStaffHandoff(
        id="h-1", institution_id="inst-1", location_id=None, contact_id="c-4",
        status="resolved",
    )
    _patch_history(
        monkeypatch,
        {id(handoff): {"status": _FakeHistory(["resolved"], ["open"])}},
    )
    session = _FakeSession([handoff])

    ise._collect(session)

    event = session.added[0]
    assert event.field == "handoff_status"
    assert (event.from_status, event.to_status) == ("open", "resolved")


def test_untracked_models_are_ignored(monkeypatch) -> None:
    class Appointment:
        def __init__(self) -> None:
            self.institution_id = "inst-1"
            self.status = "cancelled"

    appointment = Appointment()
    _patch_history(monkeypatch, {id(appointment): {}})
    session = _FakeSession([appointment])

    ise._collect(session)

    assert session.added == []


def test_untracked_attributes_on_a_tracked_model_are_ignored(monkeypatch) -> None:
    """Renaming a contact must not start a campaign."""
    contact = Contact(id="c-1", institution_id="inst-1", lead_status="new")
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory([], [])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert session.added == []


def test_a_row_without_a_tenant_is_dropped(monkeypatch) -> None:
    """An event with no institution could not be scoped to anyone's campaigns."""
    contact = Contact(id="c-1", institution_id=None, lead_status="new")
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory(["new"], [])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert session.added == []


def test_collected_events_wait_for_the_commit(monkeypatch) -> None:
    """Enqueueing during the flush would publish a change a rollback undoes."""
    contact = Contact(id="c-1", institution_id="inst-1", lead_status="booked")
    _patch_history(
        monkeypatch,
        {id(contact): {"lead_status": _FakeHistory(["booked"], ["engaged"])}},
    )
    session = _FakeSession([contact])

    ise._collect(session)

    assert len(session.info[ise._PENDING_KEY]) == 1


def test_publish_enqueues_once_per_event_and_drains(monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "src.app.tasks.automation_workflow.trigger_internal_status_workflows",
        SimpleNamespace(apply_async=lambda **kw: sent.append(kw)),
    )
    session = _FakeSession([])
    session.info[ise._PENDING_KEY] = [
        SimpleNamespace(id="e-1", institution_id="inst-1", field="contact_lead_status"),
        SimpleNamespace(id="e-2", institution_id="inst-1", field="handoff_status"),
    ]

    ise._publish(session)

    assert [call["kwargs"]["status_event_id"] for call in sent] == ["e-1", "e-2"]
    assert all(call["queue"] == "workflow" for call in sent)
    # Drained, so a second commit on the same session cannot double-fire.
    assert ise._PENDING_KEY not in session.info
    ise._publish(session)
    assert len(sent) == 2


def test_a_failed_enqueue_does_not_break_the_status_write(monkeypatch) -> None:
    """A campaign that does not start is recoverable; a 500 on the write is not."""

    def explode(**_kw):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "src.app.tasks.automation_workflow.trigger_internal_status_workflows",
        SimpleNamespace(apply_async=explode),
    )
    session = _FakeSession([])
    session.info[ise._PENDING_KEY] = [
        SimpleNamespace(id="e-1", institution_id="inst-1", field="contact_lead_status")
    ]

    ise._publish(session)  # must not raise


def test_the_listener_shields_the_write_from_its_own_bugs(monkeypatch) -> None:
    def explode(_session):
        raise RuntimeError("collector bug")

    monkeypatch.setattr(ise, "_collect", explode)

    ise._before_flush(_FakeSession([]), None, None)  # must not raise


@pytest.mark.parametrize(
    "field",
    ["call_workflow_status", "contact_lead_status", "handoff_status"],
)
def test_every_listener_field_is_selectable_on_the_trigger(field: str) -> None:
    """The listener and the trigger schema must agree on the field vocabulary."""
    from src.app.services.automation.definition_schema import InternalStatusTrigger

    trigger = InternalStatusTrigger.model_validate(
        {"type": "internal_status", "field": field, "to_statuses": ["x"]}
    )
    assert trigger.field == field
