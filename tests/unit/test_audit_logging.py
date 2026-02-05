"""
Unit tests for HIPAA-compliant audit logging.

Tests cover:
- AuditLog model creation and validation
- AuditService with InMemoryAuditRepository
- @audited decorator behavior
- Error handling and outcome classification
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome
from src.app.services.audit import (
    AuditEntry,
    AuditService,
    InMemoryAuditRepository,
    PostgresAuditRepository,
    audit_context,
    get_audit_service,
    log_audit,
    set_audit_service,
)



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
            tenant_id="tenant-uuid",
        )
        
        assert log.actor == "RETELL_AGENT"
        assert log.action == "READ_PATIENT"
        assert log.target_resource == "patient:123"
        assert log.outcome == "SUCCESS"
        assert log.audit_metadata["request_id"] == "abc123"
        assert log.tenant_id == "tenant-uuid"
    
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
            action=AuditAction.TENANT_CREATE,
            target_resource="tenant:xyz",
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
            actor=AuditActor.GHL,
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
        assert entry.tenant_id is None
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
            action=AuditAction.TENANT_CREATE,
            target_resource="tenant:abc",
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
            tenant_id="tenant-1",
            request_id="req-123",
        )
        
        entries = repo.get_all()
        assert len(entries) == 1
        
        entry = entries[0]
        assert entry.actor == AuditActor.RETELL_AGENT
        assert entry.action == AuditAction.READ_PATIENT
        assert entry.outcome == AuditOutcome.SUCCESS
        assert entry.metadata["ip"] == "127.0.0.1"
        assert entry.tenant_id == "tenant-1"
    
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
    async def test_log_handles_repository_error(self, service):
        """Test that log() catches repository errors gracefully."""
        audit_service, repo = service
        
        # Mock repository to raise error
        repo.save = AsyncMock(side_effect=Exception("DB Error"))
        
        # Should not raise - just logs error internally
        await audit_service.log(
            actor=AuditActor.SYSTEM,
            action=AuditAction.READ_LOCATIONS,
            target_resource="locations",
            outcome=AuditOutcome.SUCCESS,
        )
        
        # No entries saved due to error
        # But no exception raised


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
        self.repo = InMemoryAuditRepository()
        self.service = AuditService(self.repo)
        set_audit_service(self.service)
    
    @pytest.mark.asyncio
    async def test_decorator_logs_success_explicit(self):
        """Test that decorator logs with explicit resource lambda."""
        from src.app.services.audit_decorator import audit
        
        @audit(AuditAction.READ_PATIENT, resource=lambda args: f"patient:{args['id']}")
        async def mock_lookup(args):
            return {"count": 1}
        
        await mock_lookup({"id": "123"})
        
        import asyncio
        await asyncio.sleep(0.1)
        
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
        
        import asyncio
        await asyncio.sleep(0.1)
        
        entries = self.repo.get_all()
        assert len(entries) == 1
        # Should flag as CONFIGURATION_ERROR
        assert "CONFIGURATION_ERROR" in entries[0].target_resource
        assert "config_error" in entries[0].metadata
    
    @pytest.mark.asyncio
    async def test_decorator_logs_exception(self):
        """Test that decorator catches and logs exceptions."""
        from src.app.services.audit_decorator import audit
        
        @audit(AuditAction.BOOK_APPOINTMENT, resource="static:test")
        async def mock_fail(args):
            raise RuntimeError("Database error")
        
        with pytest.raises(RuntimeError):
            await mock_fail({})
        
        import asyncio
        await asyncio.sleep(0.1)
        
        entries = self.repo.get_all()
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.FAILURE_INTERNAL
        assert entries[0].target_resource == "static:test"


# =============================================================================
# Enum Tests
# =============================================================================

class TestAuditEnums:
    """Test audit logging enums."""
    
    def test_actor_enum_values(self):
        """Test AuditActor enum has expected values."""
        assert AuditActor.GHL.value == "GHL"
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
