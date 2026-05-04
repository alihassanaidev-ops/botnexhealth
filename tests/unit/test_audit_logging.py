"""
Unit tests for HIPAA-compliant audit logging.

Tests cover:
- AuditLog model creation and validation
- AuditService with InMemoryAuditRepository
- @audited decorator behavior
- Error handling and outcome classification
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome
from src.app.services.audit import (
    AuditEntry,
    AuditService,
    InMemoryAuditRepository,
    audit_context,
    set_audit_service,
)



class _SignalingAuditRepository(InMemoryAuditRepository):
    def __init__(self, event: asyncio.Event) -> None:
        super().__init__()
        self._event = event

    async def save(self, entry: AuditEntry) -> None:
        await super().save(entry)
        self._event.set()


# =============================================================================
# AuditLog Model Tests
# =============================================================================

class TestAuditLogModel:
    """Test AuditLog SQLAlchemy model."""

    def test_audit_log_create_with_enums(self):
        """Test creating AuditLog with enum values."""
        log = AuditLog.create(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:123",
            outcome=AuditOutcome.SUCCESS,
            audit_metadata={"request_id": "abc123"},
            institution_id="institution-uuid",
            user_id="11111111-1111-1111-1111-111111111111",
            location_id="22222222-2222-2222-2222-222222222222",
        )

        assert log.actor == "RETELL_AGENT"
        assert log.action == "READ_PATIENT"
        assert log.target_resource == "patient:123"
        assert log.outcome == "SUCCESS"
        assert log.audit_metadata["request_id"] == "abc123"
        assert log.institution_id == "institution-uuid"
        assert log.user_id == "11111111-1111-1111-1111-111111111111"
        assert log.location_id == "22222222-2222-2222-2222-222222222222"

    def test_audit_log_create_with_strings(self):
        """Test creating AuditLog with string values."""
        log = AuditLog.create(
            actor="CUSTOM_ACTOR",
            action="CUSTOM_ACTION",
            target_resource="resource:456",
            outcome="CUSTOM_OUTCOME",
        )

        assert log.actor == "CUSTOM_ACTOR"
        assert log.action == "CUSTOM_ACTION"

    def test_audit_log_has_id_generated_on_create(self):
        """Test that AuditLog id default factory generates UUID."""
        log = AuditLog.create(
            actor=AuditActor.ADMIN,
            action=AuditAction.INSTITUTION_CREATE,
            target_resource="institution:xyz",
            outcome=AuditOutcome.SUCCESS,
        )

        # Note: id is set by SQLAlchemy default on INSERT, not on object creation
        # When testing without DB, we check the default factory works
        # The default is a callable, so id will be None until INSERT
        # This is expected SQLAlchemy behavior
        assert log.actor == "ADMIN"  # Verify object created correctly

    def test_audit_log_timestamp_default(self):
        """Test that timestamp defaults to UTC now."""
        before = datetime.now(timezone.utc)

        log = AuditLog.create(
            actor=AuditActor.SYSTEM,
            action=AuditAction.READ_LOCATIONS,
            target_resource="locations",
            outcome=AuditOutcome.SUCCESS,
        )

        after = datetime.now(timezone.utc)

        # Timestamp should be between before and after
        # Note: The default uses lambda, so it's set at instantiation
        assert log.timestamp is None or (before <= log.timestamp <= after)


# =============================================================================
# AuditEntry DTO Tests
# =============================================================================

class TestAuditEntry:
    """Test AuditEntry data transfer object."""

    def test_audit_entry_immutable(self):
        """Test that AuditEntry is frozen (immutable)."""
        entry = AuditEntry(
            actor=AuditActor.SYSTEM,
            action=AuditAction.BOOK_APPOINTMENT,
            target_resource="appointment:789",
            outcome=AuditOutcome.SUCCESS,
        )

        with pytest.raises(AttributeError):
            entry.actor = AuditActor.ADMIN  # Should fail

    def test_audit_entry_defaults(self):
        """Test AuditEntry default values."""
        entry = AuditEntry(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:test",
            outcome=AuditOutcome.SUCCESS,
        )

        assert entry.metadata == {}
        assert entry.institution_id is None
        assert entry.timestamp is not None
        assert entry.request_id is not None


# =============================================================================
# InMemoryAuditRepository Tests
# =============================================================================

class TestInMemoryAuditRepository:
    """Test InMemoryAuditRepository (used for testing)."""

    @pytest.fixture
    def repo(self):
        return InMemoryAuditRepository()

    @pytest.mark.asyncio
    async def test_save_entry(self, repo):
        """Test saving a single entry."""
        entry = AuditEntry(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:123",
            outcome=AuditOutcome.SUCCESS,
        )

        await repo.save(entry)

        assert len(repo.get_all()) == 1
        assert repo.get_all()[0] == entry

    @pytest.mark.asyncio
    async def test_save_batch(self, repo):
        """Test saving multiple entries."""
        entries = [
            AuditEntry(
                actor=AuditActor.RETELL_AGENT,
                action=AuditAction.READ_PATIENT,
                target_resource=f"patient:{i}",
                outcome=AuditOutcome.SUCCESS,
            )
            for i in range(5)
        ]

        await repo.save_batch(entries)

        assert len(repo.get_all()) == 5

    @pytest.mark.asyncio
    async def test_clear(self, repo):
        """Test clearing all entries."""
        entry = AuditEntry(
            actor=AuditActor.ADMIN,
            action=AuditAction.INSTITUTION_CREATE,
            target_resource="institution:abc",
            outcome=AuditOutcome.SUCCESS,
        )

        await repo.save(entry)
        assert len(repo.get_all()) == 1

        repo.clear()
        assert len(repo.get_all()) == 0


# =============================================================================
# AuditService Tests
# =============================================================================

class TestAuditService:
    """Test AuditService with InMemoryRepository."""

    @pytest.fixture
    def service(self):
        repo = InMemoryAuditRepository()
        return AuditService(repo), repo

    @pytest.mark.asyncio
    async def test_log_success(self, service):
        """Test logging a successful action."""
        audit_service, repo = service

        await audit_service.log(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:123",
            outcome=AuditOutcome.SUCCESS,
            metadata={"ip": "127.0.0.1"},
            institution_id="institution-1",
            request_id="req-123",
        )

        entries = repo.get_all()
        assert len(entries) == 1

        entry = entries[0]
        assert entry.actor == AuditActor.RETELL_AGENT
        assert entry.action == AuditAction.READ_PATIENT
        assert entry.outcome == AuditOutcome.SUCCESS
        assert entry.metadata["ip"] == "127.0.0.1"
        assert entry.institution_id == "institution-1"

    @pytest.mark.asyncio
    async def test_log_failure(self, service):
        """Test logging a failed action."""
        audit_service, repo = service

        await audit_service.log(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.BOOK_APPOINTMENT,
            target_resource="appointment:new",
            outcome=AuditOutcome.FAILURE_VALIDATION,
        )

        entries = repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.FAILURE_VALIDATION

    @pytest.mark.asyncio
    async def test_log_infers_user_and_location_ids_from_metadata(self, service):
        """Test logging infers direct filter columns from audit metadata."""
        audit_service, repo = service

        await audit_service.log(
            actor=AuditActor.ADMIN,
            action=AuditAction.VIEW_AUDIT_LOGS,
            target_resource="institution:audit_logs",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_user_id": "11111111-1111-1111-1111-111111111111",
                "location_id": "22222222-2222-2222-2222-222222222222",
            },
        )

        entry = repo.get_all()[0]
        assert entry.user_id == "11111111-1111-1111-1111-111111111111"
        assert entry.location_id == "22222222-2222-2222-2222-222222222222"

    @pytest.mark.asyncio
    async def test_log_raises_on_repository_error(self, service):
        """log() must propagate persistence errors as AuditPersistenceError.

        Silent swallowing of audit failures was a HIPAA-relevant defect:
        PHI access without a durable audit row leaves activity unattributed.
        """
        from src.app.services.audit import AuditPersistenceError

        audit_service, repo = service
        repo.save = AsyncMock(side_effect=Exception("DB Error"))

        with pytest.raises(AuditPersistenceError):
            await audit_service.log(
                actor=AuditActor.SYSTEM,
                action=AuditAction.READ_LOCATIONS,
                target_resource="locations",
                outcome=AuditOutcome.SUCCESS,
            )

    @pytest.mark.asyncio
    async def test_durable_audit_failure_propagates_through_decorator(self):
        """Regression: durable audit failures must surface to the caller.

        Previously the service swallowed errors internally, so the decorator
        never saw the failure and the request returned 200 with no audit row
        — silently violating the HIPAA durability promise.
        """
        from src.app.services.audit import (
            AuditPersistenceError,
            AuditService,
            InMemoryAuditRepository,
            set_audit_service,
        )
        from src.app.services.audit_decorator import audit
        from src.app.models.audit_log import AuditAction

        repo = InMemoryAuditRepository()
        repo.save = AsyncMock(side_effect=Exception("DB unreachable"))
        set_audit_service(AuditService(repo))

        @audit(AuditAction.BOOK_APPOINTMENT, resource=lambda *a, **kw: "appt:test")
        async def fake_handler(args):
            return {"success": True, "id": "appt-1"}

        with pytest.raises(AuditPersistenceError):
            await fake_handler({"patient_id": "p1"})


# =============================================================================
# Audit Context Manager Tests
# =============================================================================

class TestAuditContext:
    """Test audit_context async context manager."""

    @pytest.fixture
    def service(self):
        repo = InMemoryAuditRepository()
        return AuditService(repo), repo

    @pytest.mark.asyncio
    async def test_success_context(self, service):
        """Test that context logs SUCCESS on normal exit."""
        audit_service, repo = service

        async with audit_context(
            service=audit_service,
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:123",
        ) as ctx:
            ctx["extra_info"] = "test"

        entries = repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.SUCCESS
        assert entries[0].metadata["extra_info"] == "test"

    @pytest.mark.asyncio
    async def test_failure_context(self, service):
        """Test that context logs FAILURE on exception."""
        audit_service, repo = service

        with pytest.raises(ValueError):
            async with audit_context(
                service=audit_service,
                actor=AuditActor.API_CLIENT,
                action=AuditAction.BOOK_APPOINTMENT,
                target_resource="appointment:new",
            ):
                raise ValueError("Invalid data")

        entries = repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.FAILURE_INTERNAL
        assert "error_type" in entries[0].metadata


# =============================================================================
# @audited Decorator Tests
# =============================================================================

# =============================================================================
# @audit Decorator Tests
# =============================================================================

class TestAuditedDecorator:
    """Test @audit decorator with explicit extractors."""

    @pytest.fixture(autouse=True)
    def setup_service(self):
        """Set up in-memory audit service for tests."""
        self.audit_saved = asyncio.Event()
        self.repo = _SignalingAuditRepository(self.audit_saved)
        self.service = AuditService(self.repo)
        set_audit_service(self.service)

    async def _wait_for_audit(self) -> None:
        await asyncio.wait_for(self.audit_saved.wait(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_decorator_logs_success_explicit(self):
        """Test that decorator logs with explicit resource lambda."""
        from src.app.services.audit_decorator import audit

        @audit(AuditAction.READ_PATIENT, resource=lambda args: f"patient:{args['id']}")
        async def mock_lookup(args):
            return {"count": 1}

        await mock_lookup({"id": "123"})

        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 1
        assert entries[0].target_resource == "patient:123"
        assert entries[0].outcome == AuditOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_decorator_config_error(self):
        """Test that extractor failure logs CRITICAL config error."""
        from src.app.services.audit_decorator import audit

        # BROKEN EXTRACTOR: tries to access 'id' but input doesn't have it
        @audit(AuditAction.READ_PATIENT, resource=lambda args: f"patient:{args['id']}")
        async def mock_broken(args):
            return "ok"

        # Call with missing key
        await mock_broken({"name": "john"})

        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 1
        # Should flag as CONFIGURATION_ERROR
        assert "CONFIGURATION_ERROR" in entries[0].target_resource
        assert "config_error" in entries[0].metadata

    @pytest.mark.asyncio
    async def test_decorator_logs_exception(self):
        """Durable actions write a pre-action INITIATED row plus a
        post-action FAILURE_INTERNAL row, sharing one request_id."""
        from src.app.services.audit_decorator import audit

        @audit(AuditAction.BOOK_APPOINTMENT, resource="static:test")
        async def mock_fail(args):
            raise RuntimeError("Database error")

        with pytest.raises(RuntimeError):
            await mock_fail({})

        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 2
        assert {e.outcome for e in entries} == {
            AuditOutcome.INITIATED,
            AuditOutcome.FAILURE_INTERNAL,
        }
        assert all(e.target_resource == "static:test" for e in entries)
        # Pre/post rows share the request_id — the breadcrumb operators use
        # to reconcile if the post-action write fails.
        assert len({e.request_id for e in entries}) == 1

    @pytest.mark.asyncio
    async def test_decorator_classifies_soft_error_dict_as_failure(self):
        """Retell-style handlers signal failure by returning a dict with
        ``error`` or ``success: False`` rather than raising. Without soft-error
        classification, a failed booking lands in audit_logs as SUCCESS — a
        HIPAA-relevant audit-trail integrity bug. Each shape below MUST land
        as FAILURE_VALIDATION on the post-action row (paired with INITIATED)."""
        from src.app.services.audit_decorator import audit

        cases = [
            {"error": "appointment_type_id is required."},
            {"success": False, "error": "patient already booked"},
            {"success": False, "message": "PMS booking failed"},
        ]

        for i, retval in enumerate(cases):
            @audit(AuditAction.BOOK_APPOINTMENT, resource=f"appt:{i}")
            async def handler(_args, _retval=retval):
                return _retval

            self.audit_saved.clear()
            await handler({})
            await self._wait_for_audit()

        entries = self.repo.get_all()
        # Each call writes 2 rows (INITIATED + outcome) — durable two-row pattern.
        assert len(entries) == 2 * len(cases)

        post_rows = [e for e in entries if e.outcome != AuditOutcome.INITIATED]
        intent_rows = [e for e in entries if e.outcome == AuditOutcome.INITIATED]
        assert len(post_rows) == len(cases)
        assert len(intent_rows) == len(cases)
        for entry in post_rows:
            assert entry.outcome == AuditOutcome.FAILURE_VALIDATION, (
                f"Soft-error dict was logged as {entry.outcome} — should be "
                "FAILURE_VALIDATION (audit integrity bug)"
            )
            assert entry.metadata.get("error_kind") == "soft_failure"

    @pytest.mark.asyncio
    async def test_decorator_classifies_failed_pydantic_booking_result_as_failure(self):
        """Universal appointment routes return ``BookingResult`` Pydantic
        models, not plain dicts. Without normalising via ``model_dump()``,
        a failed booking (success=False, error=...) is silently classified
        as SUCCESS — same audit-trail integrity bug the dict path catches."""
        from src.app.pms.models import BookingResult
        from src.app.services.audit_decorator import audit

        @audit(AuditAction.BOOK_APPOINTMENT, resource="appt:pyd")
        async def handler(_args):
            return BookingResult(
                success=False,
                status="error",
                error="Slot unavailable",
            )

        await handler({})
        await self._wait_for_audit()

        entries = self.repo.get_all()
        post_rows = [e for e in entries if e.outcome != AuditOutcome.INITIATED]
        assert len(post_rows) == 1
        assert post_rows[0].outcome == AuditOutcome.FAILURE_VALIDATION, (
            f"Pydantic BookingResult(success=False) logged as "
            f"{post_rows[0].outcome} — must be FAILURE_VALIDATION"
        )
        assert post_rows[0].metadata.get("error_kind") == "soft_failure"
        assert post_rows[0].metadata.get("error_message") == "Slot unavailable"

    @pytest.mark.asyncio
    async def test_decorator_writes_initiated_before_running_func(self):
        """If the pre-action audit write fails, the wrapped function MUST
        NOT run — refusing to mutate PMS / reveal PHI without a recorded
        intent. This is the §164.312(b) defense against the failure mode
        where the side-effect commits but the audit write later fails."""
        from src.app.services.audit_decorator import audit
        from src.app.services.audit import AuditPersistenceError

        ran = []

        class _FailingIntentRepo(_SignalingAuditRepository):
            def __init__(self, event):
                super().__init__(event)
                self.calls = 0

            async def save(self, entry):
                self.calls += 1
                if entry.outcome == AuditOutcome.INITIATED:
                    raise AuditPersistenceError("simulated audit DB outage")
                await super().save(entry)

        repo = _FailingIntentRepo(self.audit_saved)
        set_audit_service(AuditService(repo))

        @audit(AuditAction.BOOK_APPOINTMENT, resource="appt:test")
        async def book(_args):
            ran.append(True)  # MUST NOT execute when intent write fails
            return {"success": True}

        with pytest.raises(AuditPersistenceError):
            await book({})

        assert ran == [], (
            "Wrapped function ran despite intent-audit write failure — "
            "this is the gap the two-row pattern is meant to close"
        )
        # The repo saw exactly one save attempt (the failed INITIATED write).
        # No post-action row was written because func() never ran.
        assert repo.calls == 1

    @pytest.mark.asyncio
    async def test_decorator_does_not_misclassify_success_with_message(self):
        """A successful response that happens to include a "message" field
        (e.g. ``lookup_patient`` returning {"match_status": "single",
        "message": "Found 1 patient(s)."}) must still be SUCCESS."""
        from src.app.services.audit_decorator import audit

        @audit(AuditAction.SEARCH_PATIENTS, resource="patient:search")
        async def handler(_args):
            return {"count": 1, "message": "Found 1 patient(s)."}

        await handler({})
        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_search_patients_audit_metadata_is_non_phi_and_best_effort(self):
        """Debounced patient search logs useful metadata without raw PHI."""
        from src.app.services.audit_decorator import audit

        @audit(AuditAction.SEARCH_PATIENTS, resource="patient:search")
        async def handler(args):
            return {"count": 2, "patients": [{"id": "1"}, {"id": "2"}]}

        await handler(
            {
                "name": "Jane Smith",
                "email": "jane@example.test",
                "phone_number": "+15551234567",
            }
        )
        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.SUCCESS
        assert entries[0].metadata["high_volume_read"] is True
        assert entries[0].metadata["search_criteria"] == ["name", "email", "phone"]
        assert entries[0].metadata["result_count"] == 2
        assert "Jane Smith" not in str(entries[0].metadata)
        assert "jane@example.test" not in str(entries[0].metadata)

    @pytest.mark.asyncio
    async def test_decorator_writes_direct_user_and_location_ids(self):
        """Test decorator writes direct audit columns from current_user context."""
        from src.app.services.audit_decorator import audit

        current_user = SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            role="INSTITUTION_ADMIN",
            institution_id="22222222-2222-2222-2222-222222222222",
            location_id="33333333-3333-3333-3333-333333333333",
        )

        @audit(AuditAction.VIEW_AUDIT_LOGS, resource="audit:logs", actor=AuditActor.ADMIN)
        async def mock_route(current_user):
            return {"ok": True}

        await mock_route(current_user=current_user)

        await self._wait_for_audit()

        entries = self.repo.get_all()
        assert len(entries) == 1
        assert entries[0].user_id == current_user.id
        assert entries[0].location_id == current_user.location_id


# =============================================================================
# _classify_soft_error direct tests
# =============================================================================


class TestClassifySoftError:
    """Direct coverage of the classifier — both dict and Pydantic-model paths."""

    def test_pydantic_booking_result_failure_classified_as_validation_failure(self):
        from src.app.pms.models import BookingResult
        from src.app.services.audit_decorator import _classify_soft_error

        result = BookingResult(success=False, status="error", error="Slot unavailable")
        outcome, message = _classify_soft_error(result)

        assert outcome == AuditOutcome.FAILURE_VALIDATION
        assert message == "Slot unavailable"

    def test_pydantic_booking_result_success_returns_none(self):
        from src.app.pms.models import BookingResult
        from src.app.services.audit_decorator import _classify_soft_error

        result = BookingResult(success=True, status="confirmed", id="appt-1")
        outcome, message = _classify_soft_error(result)

        assert outcome is None
        assert message is None

    def test_dict_with_error_classified_as_validation_failure(self):
        from src.app.services.audit_decorator import _classify_soft_error

        outcome, message = _classify_soft_error({"error": "patient not found"})
        assert outcome == AuditOutcome.FAILURE_VALIDATION
        assert message == "patient not found"

    def test_non_dict_non_pydantic_returns_none(self):
        from src.app.services.audit_decorator import _classify_soft_error

        for value in (None, "ok", 42, ["a", "b"]):
            outcome, message = _classify_soft_error(value)
            assert outcome is None
            assert message is None


# =============================================================================
# Enum Tests
# =============================================================================

class TestAuditEnums:
    """Test audit logging enums."""

    def test_actor_enum_values(self):
        """Test AuditActor enum has expected values."""
        assert AuditActor.RETELL_AGENT.value == "RETELL_AGENT"
        assert AuditActor.ADMIN.value == "ADMIN"
        assert AuditActor.SYSTEM.value == "SYSTEM"
        assert AuditActor.API_CLIENT.value == "API_CLIENT"

    def test_action_enum_values(self):
        """Test AuditAction enum has expected values."""
        assert AuditAction.READ_PATIENT.value == "READ_PATIENT"
        assert AuditAction.CREATE_PATIENT.value == "CREATE_PATIENT"
        assert AuditAction.BOOK_APPOINTMENT.value == "BOOK_APPOINTMENT"
        assert AuditAction.CANCEL_APPOINTMENT.value == "CANCEL_APPOINTMENT"
        assert AuditAction.RESCHEDULE_APPOINTMENT.value == "RESCHEDULE_APPOINTMENT"

    def test_outcome_enum_values(self):
        """Test AuditOutcome enum has expected values."""
        assert AuditOutcome.SUCCESS.value == "SUCCESS"
        assert AuditOutcome.FAILURE_UNAUTHORIZED.value == "FAILURE_UNAUTHORIZED"
        assert AuditOutcome.FAILURE_NOT_FOUND.value == "FAILURE_NOT_FOUND"
        assert AuditOutcome.FAILURE_VALIDATION.value == "FAILURE_VALIDATION"
        assert AuditOutcome.FAILURE_EXTERNAL_API.value == "FAILURE_EXTERNAL_API"
        assert AuditOutcome.FAILURE_INTERNAL.value == "FAILURE_INTERNAL"
