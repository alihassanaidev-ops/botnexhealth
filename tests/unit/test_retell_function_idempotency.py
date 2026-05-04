"""Unit tests for Retell function-call idempotency."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.retell import functions, idempotency
from src.app.retell.idempotency import (
    IDEMPOTENT_FUNCTIONS,
    canonical_args_hash,
    run_with_idempotency,
)


# ---------------------------------------------------------------------------
# Fake DB session that mimics the unique-constraint behavior of the real table
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, *, call_id: str, function_name: str, args_hash: str) -> None:
        self.call_id = call_id
        self.function_name = function_name
        self.args_hash = args_hash
        self.status = "PROCESSING"
        self.attempts = 1
        self.institution_id: str | None = None
        self.result_json: str | None = None
        self.last_error: str | None = None
        self.created_at = None
        self.updated_at = None


class _FakeStore:
    """In-memory stand-in for the retell_function_invocations table."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], _FakeRow] = {}

    def find(self, call_id: str, function_name: str, args_hash: str) -> _FakeRow | None:
        return self.rows.get((call_id, function_name, args_hash))

    def insert(self, row: _FakeRow) -> bool:
        key = (row.call_id, row.function_name, row.args_hash)
        if key in self.rows:
            return False
        self.rows[key] = row
        return True


class _FakeSelectStmt:
    def __init__(self, where_clauses: list[Any]) -> None:
        self.where_clauses = where_clauses


class _FakeExecuteResult:
    def __init__(self, value: _FakeRow | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> _FakeRow | None:
        return self.value


class _FakeSession:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store
        self._pending: _FakeRow | None = None
        self._lookup_args: dict[str, str] = {}

    def _capture_lookup(self, where_args: tuple) -> None:
        # Each where arg looks like Column == value; we read the right side.
        self._lookup_args = {}
        for clause in where_args:
            try:
                col_name = clause.left.key  # type: ignore[attr-defined]
                value = clause.right.value  # type: ignore[attr-defined]
            except AttributeError:
                continue
            self._lookup_args[col_name] = value

    async def execute(self, stmt) -> _FakeExecuteResult:
        # Recorded select(...).where(...) — extract the equality clauses
        where_clauses = list(stmt.whereclause.clauses) if stmt.whereclause is not None else []  # type: ignore[attr-defined]
        self._capture_lookup(tuple(where_clauses))
        row = self.store.find(
            self._lookup_args.get("call_id", ""),
            self._lookup_args.get("function_name", ""),
            self._lookup_args.get("args_hash", ""),
        )
        return _FakeExecuteResult(row)

    def add(self, row: _FakeRow) -> None:
        self._pending = row

    async def flush(self) -> None:
        if self._pending is None:
            return
        if not self.store.insert(self._pending):
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("duplicate", {}, Exception("uq violation"))
        self._pending = None

    async def rollback(self) -> None:
        self._pending = None

    async def commit(self) -> None:
        # Explicit-commit path used by run_with_idempotency to make claims
        # durable; in this in-memory store there is nothing to flush further.
        return None


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    session = _FakeSession(store)
    monkeypatch.setattr(
        "src.app.database.get_db_session",
        lambda: _FakeSessionContext(session),
    )

    # Patch the model class import inside idempotency.py so attribute lookups
    # like RetellFunctionInvocation(...) build a _FakeRow instead.
    class _StubModel:
        def __init__(self, **kwargs: Any) -> None:
            row = _FakeRow(
                call_id=kwargs["call_id"],
                function_name=kwargs["function_name"],
                args_hash=kwargs["args_hash"],
            )
            row.status = kwargs.get("status", "PROCESSING")
            row.attempts = kwargs.get("attempts", 1)
            self._row = row

        # Allow `session.add(row)` to find the underlying record.
        def __getattr__(self, name: str) -> Any:
            return getattr(self._row, name)

    return store


# ---------------------------------------------------------------------------
# Hash semantics
# ---------------------------------------------------------------------------


def test_canonical_args_hash_is_order_independent():
    a = {"patient_id": "p1", "provider_id": "pr1", "start_time": "2026-05-04T09:00"}
    b = {"start_time": "2026-05-04T09:00", "provider_id": "pr1", "patient_id": "p1"}
    assert canonical_args_hash(a) == canonical_args_hash(b)


def test_canonical_args_hash_differs_for_different_args():
    base = {"patient_id": "p1", "start_time": "2026-05-04T09:00"}
    changed = {"patient_id": "p1", "start_time": "2026-05-04T10:00"}
    assert canonical_args_hash(base) != canonical_args_hash(changed)


def test_canonical_args_hash_handles_non_dict_input():
    # Must not crash on unexpected shapes.
    assert canonical_args_hash([1, 2, 3]) == canonical_args_hash([1, 2, 3])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Lifecycle: first call, replay, in-flight duplicate, retry-on-failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_runs_handler_and_records_completed(fake_store: _FakeStore):
    handler = AsyncMock(return_value={"success": True, "appointment_id": "apt-1"})
    args = {"patient_id": "p1", "provider_id": "pr1", "start_time": "T"}

    result = await run_with_idempotency(
        handler,
        function_name="book_appointment",
        call_id="call-1",
        args=args,
    )

    assert result == {"success": True, "appointment_id": "apt-1"}
    handler.assert_awaited_once_with(args)

    row = fake_store.find("call-1", "book_appointment", canonical_args_hash(args))
    assert row is not None
    assert row.status == "COMPLETED"
    assert json.loads(row.result_json or "null") == result


@pytest.mark.asyncio
async def test_replay_returns_cached_result_without_calling_handler(fake_store: _FakeStore):
    args = {"patient_id": "p1", "provider_id": "pr1", "start_time": "T"}
    first = AsyncMock(return_value={"success": True, "appointment_id": "apt-1"})
    await run_with_idempotency(
        first, function_name="book_appointment", call_id="call-1", args=args
    )

    replay_handler = AsyncMock(return_value={"success": True, "appointment_id": "DIFFERENT"})
    result = await run_with_idempotency(
        replay_handler,
        function_name="book_appointment",
        call_id="call-1",
        args=args,
    )

    assert result == {"success": True, "appointment_id": "apt-1"}
    replay_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_different_args_produces_separate_invocations(fake_store: _FakeStore):
    handler = AsyncMock(side_effect=[{"id": 1}, {"id": 2}])

    await run_with_idempotency(
        handler,
        function_name="book_appointment",
        call_id="call-1",
        args={"start_time": "09:00"},
    )
    await run_with_idempotency(
        handler,
        function_name="book_appointment",
        call_id="call-1",
        args={"start_time": "10:00"},
    )

    assert handler.await_count == 2
    assert len(fake_store.rows) == 2


@pytest.mark.asyncio
async def test_unknown_call_id_rejected_for_idempotent_function(fake_store: _FakeStore):
    """A booking call without a usable call_id must be rejected outright.

    Bypassing idempotency for booking would allow Retell network blips to
    produce duplicate bookings — so the system fails loud rather than
    silently disabling the safety net.
    """
    from fastapi import HTTPException

    handler = AsyncMock(return_value={"ok": True})

    with pytest.raises(HTTPException) as exc:
        await run_with_idempotency(
            handler,
            function_name="book_appointment",
            call_id="unknown_call_id",
            args={"x": 1},
        )

    assert exc.value.status_code == 400
    handler.assert_not_awaited()
    assert fake_store.rows == {}


@pytest.mark.asyncio
async def test_unknown_call_id_allowed_for_non_idempotent_function(fake_store: _FakeStore):
    """Read-only (non-idempotent) functions still tolerate missing call_id."""
    handler = AsyncMock(return_value={"ok": True})

    result = await run_with_idempotency(
        handler,
        function_name="lookup_patient",
        call_id="unknown_call_id",
        args={"x": 1},
    )

    assert result == {"ok": True}
    handler.assert_awaited_once()
    assert fake_store.rows == {}


@pytest.mark.asyncio
async def test_in_flight_duplicate_returns_retryable_error(fake_store: _FakeStore):
    args = {"x": 1}
    args_hash = canonical_args_hash(args)
    # Simulate first request still PROCESSING by inserting directly.
    row = _FakeRow(call_id="call-1", function_name="book_appointment", args_hash=args_hash)
    fake_store.insert(row)

    handler = AsyncMock(return_value={"success": True})
    result = await run_with_idempotency(
        handler,
        function_name="book_appointment",
        call_id="call-1",
        args=args,
    )

    assert result["error"] == "still_processing"
    assert result["retryable"] is True
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_invocation_can_be_retried_and_clears_error(fake_store: _FakeStore):
    args = {"x": 1}
    args_hash = canonical_args_hash(args)
    failed_row = _FakeRow(call_id="call-1", function_name="book_appointment", args_hash=args_hash)
    failed_row.status = "FAILED"
    failed_row.last_error = "timeout"
    failed_row.attempts = 1
    fake_store.insert(failed_row)

    handler = AsyncMock(return_value={"success": True, "appointment_id": "apt-1"})
    result = await run_with_idempotency(
        handler,
        function_name="book_appointment",
        call_id="call-1",
        args=args,
    )

    assert result == {"success": True, "appointment_id": "apt-1"}
    handler.assert_awaited_once()
    row = fake_store.find("call-1", "book_appointment", args_hash)
    assert row is not None
    assert row.status == "COMPLETED"
    assert row.attempts == 2
    assert row.last_error is None


@pytest.mark.asyncio
async def test_handler_exception_records_failed_status(fake_store: _FakeStore):
    args = {"x": 1}
    handler = AsyncMock(side_effect=RuntimeError("PMS down"))

    with pytest.raises(RuntimeError):
        await run_with_idempotency(
            handler,
            function_name="book_appointment",
            call_id="call-1",
            args=args,
        )

    row = fake_store.find("call-1", "book_appointment", canonical_args_hash(args))
    assert row is not None
    assert row.status == "FAILED"
    assert row.last_error == "PMS down"


# ---------------------------------------------------------------------------
# Dispatcher allowlist
# ---------------------------------------------------------------------------


def test_idempotent_allowlist_covers_only_mutating_functions():
    assert IDEMPOTENT_FUNCTIONS == frozenset(
        {
            "book_appointment",
            "cancel_appointment",
            "reschedule_appointment",
            "create_patient",
        }
    )
    assert "lookup_patient" not in IDEMPOTENT_FUNCTIONS
    assert "find_appointment_slots" not in IDEMPOTENT_FUNCTIONS


@pytest.fixture
def isolated_registry():
    orig = functions._function_registry.copy()
    functions._function_registry.clear()
    yield functions._function_registry
    functions._function_registry.clear()
    functions._function_registry.update(orig)


@pytest.mark.asyncio
async def test_dispatcher_routes_mutating_function_through_idempotency(isolated_registry):
    handler = AsyncMock(return_value={"success": True})
    isolated_registry["book_appointment"] = handler

    payload = {
        "function_name": "book_appointment",
        "call_id": "call-1",
        "args": {"patient_id": "p1"},
    }
    body = json.dumps(payload).encode()

    with patch.object(
        functions, "run_with_idempotency", new=AsyncMock(return_value={"success": True})
    ) as wrap:
        await functions.handle_function_call(body=body)

    wrap.assert_awaited_once()
    call_kwargs = wrap.await_args.kwargs
    assert call_kwargs["function_name"] == "book_appointment"
    assert call_kwargs["call_id"] == "call-1"
    assert call_kwargs["args"] == {"patient_id": "p1"}
    handler.assert_not_awaited()  # idempotency wrapper invoked instead


@pytest.mark.asyncio
async def test_dispatcher_skips_idempotency_for_read_only_function(isolated_registry):
    handler = AsyncMock(return_value={"patients": []})
    isolated_registry["lookup_patient"] = handler

    payload = {
        "function_name": "lookup_patient",
        "call_id": "call-1",
        "args": {"name": "John"},
    }
    body = json.dumps(payload).encode()

    with patch.object(functions, "run_with_idempotency", new=AsyncMock()) as wrap:
        await functions.handle_function_call(body=body)

    wrap.assert_not_awaited()
    handler.assert_awaited_once_with({"name": "John"})
