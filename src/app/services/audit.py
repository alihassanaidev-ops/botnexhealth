"""
Audit logging service with SOLID architecture.

SOLID Principles Applied:
- SRP: Each class has a single responsibility
- OCP: New storage backends can be added without modifying existing code
- LSP: Concrete repositories can substitute for the abstract interface
- ISP: Small, focused interfaces (IAuditRepository)
- DIP: Service depends on abstraction (IAuditRepository), not concrete implementations

Architecture:
    ┌─────────────────┐
    │  AuditService   │  ← High-level business logic
    └────────┬────────┘
             │ depends on
             ▼
    ┌─────────────────┐
    │ IAuditRepository│  ← Abstraction (Protocol)
    └────────┬────────┘
             │ implemented by
             ▼
    ┌─────────────────┐
    │PostgresAuditRepo│  ← Concrete implementation
    └─────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Protocol, runtime_checkable
from uuid import uuid4

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome

logger = logging.getLogger(__name__)


# =============================================================================
# Data Transfer Objects (DTOs)
# =============================================================================

@dataclass(frozen=True)
class AuditEntry:
    """
    Immutable data transfer object for audit entries.
    
    Decouples service layer from database model (DIP).
    """
    actor: AuditActor | str
    action: AuditAction | str
    target_resource: str
    outcome: AuditOutcome | str
    metadata: dict[str, Any] = field(default_factory=dict)
    institution_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str = field(default_factory=lambda: str(uuid4()))


# =============================================================================
# Repository Interface (ISP + DIP)
# =============================================================================

@runtime_checkable
class IAuditRepository(Protocol):
    """
    Interface for audit log persistence.
    
    ISP: Small, focused interface with only necessary methods.
    DIP: High-level modules depend on this abstraction.
    
    Implementations can be:
    - PostgresAuditRepository (primary)
    - InMemoryAuditRepository (testing)
    - S3AuditRepository (future: immutable archive)
    - MongoAuditRepository (future: alternative storage)
    """
    
    async def save(self, entry: AuditEntry) -> None:
        """Persist an audit entry."""
        ...
    
    async def save_batch(self, entries: list[AuditEntry]) -> None:
        """Persist multiple audit entries atomically."""
        ...


# =============================================================================
# Concrete Repository Implementation (LSP)
# =============================================================================

class PostgresAuditRepository:
    """
    PostgreSQL implementation of audit repository.
    
    LSP: Can substitute for IAuditRepository in any context.
    SRP: Only responsible for database persistence.
    """
    
    async def save(self, entry: AuditEntry) -> None:
        """Persist a single audit entry to PostgreSQL."""
        from src.app.database import get_db_session
        
        audit_log = AuditLog.create(
            actor=entry.actor,
            action=entry.action,
            target_resource=entry.target_resource,
            outcome=entry.outcome,
            audit_metadata={
                "request_id": entry.request_id,
                **entry.metadata
            },
            institution_id=entry.institution_id,
        )
        # Override timestamp if provided
        audit_log.timestamp = entry.timestamp
        
        async with get_db_session() as session:
            session.add(audit_log)
            # Commit happens automatically on context exit
    
    async def save_batch(self, entries: list[AuditEntry]) -> None:
        """Persist multiple audit entries atomically."""
        from src.app.database import get_db_session
        
        async with get_db_session() as session:
            for entry in entries:
                audit_log = AuditLog.create(
                    actor=entry.actor,
                    action=entry.action,
                    target_resource=entry.target_resource,
                    outcome=entry.outcome,
                    audit_metadata={
                        "request_id": entry.request_id,
                        **entry.metadata
                    },
                    institution_id=entry.institution_id,
                )
                audit_log.timestamp = entry.timestamp
                session.add(audit_log)


class InMemoryAuditRepository:
    """
    In-memory implementation for testing.
    
    LSP: Can substitute for IAuditRepository.
    """
    
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []
    
    async def save(self, entry: AuditEntry) -> None:
        """Store entry in memory."""
        self.entries.append(entry)
    
    async def save_batch(self, entries: list[AuditEntry]) -> None:
        """Store multiple entries in memory."""
        self.entries.extend(entries)
    
    def clear(self) -> None:
        """Clear all entries (for test cleanup)."""
        self.entries.clear()
    
    def get_all(self) -> list[AuditEntry]:
        """Get all stored entries (for test assertions)."""
        return list(self.entries)


# =============================================================================
# Audit Service (SRP + DIP)
# =============================================================================

class AuditService:
    """
    High-level audit logging service.
    
    SRP: Orchestrates audit logging with fire-and-forget capability.
    DIP: Depends on IAuditRepository abstraction, not concrete implementation.
    
    Usage:
        service = AuditService(PostgresAuditRepository())
        await service.log(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:123",
            outcome=AuditOutcome.SUCCESS,
        )
    """
    
    def __init__(self, repository: IAuditRepository) -> None:
        """
        Initialize with a repository implementation.
        
        DIP: Accept abstraction, not concretion.
        """
        self._repository = repository
    
    async def log(
        self,
        actor: AuditActor | str,
        action: AuditAction | str,
        target_resource: str,
        outcome: AuditOutcome | str,
        metadata: dict[str, Any] | None = None,
        institution_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log an audit entry.
        
        This is a fire-and-forget operation - failures are logged but don't
        propagate to the caller to avoid disrupting the main request flow.
        """
        entry = AuditEntry(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=outcome,
            metadata=metadata or {},
            institution_id=institution_id,
            request_id=request_id or str(uuid4()),
        )
        
        try:
            await self._repository.save(entry)
            logger.debug(
                f"Audit logged: {action} on {target_resource} by {actor} => {outcome}"
            )
        except Exception as e:
            # Never let audit failures break the main request
            logger.error(f"Failed to save audit log: {e}", exc_info=True)
    
    def log_background(
        self,
        actor: AuditActor | str,
        action: AuditAction | str,
        target_resource: str,
        outcome: AuditOutcome | str,
        metadata: dict[str, Any] | None = None,
        institution_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log an audit entry in the background (non-blocking).
        
        Use this for maximum performance when you don't need to wait.
        """
        asyncio.create_task(
            self.log(
                actor=actor,
                action=action,
                target_resource=target_resource,
                outcome=outcome,
                metadata=metadata,
                institution_id=institution_id,
                request_id=request_id,
            )
        )


# =============================================================================
# Audit Context Manager (OCP)
# =============================================================================

@asynccontextmanager
async def audit_context(
    service: AuditService,
    actor: AuditActor | str,
    action: AuditAction | str,
    target_resource: str,
    metadata: dict[str, Any] | None = None,
    institution_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Context manager for automatic audit logging with outcome tracking.
    
    OCP: Extends audit functionality without modifying AuditService.
    
    Usage:
        async with audit_context(service, actor, action, resource) as ctx:
            # Do work...
            ctx["extra_data"] = "value"
        # Automatically logs SUCCESS on normal exit, FAILURE on exception
    
    Args:
        service: AuditService instance
        actor: Who is performing the action
        action: What action is being performed
        target_resource: What resource is being accessed
        metadata: Additional context
        institution_id: Optional institution ID
    
    Yields:
        A mutable dict for adding extra metadata during the operation
    """
    request_id = str(uuid4())
    extra_metadata: dict[str, Any] = {}
    
    try:
        yield extra_metadata
        # If we get here, operation succeeded
        await service.log(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=AuditOutcome.SUCCESS,
            metadata={**(metadata or {}), **extra_metadata},
            institution_id=institution_id,
            request_id=request_id,
        )
    except Exception as e:
        # Determine failure type
        outcome = _classify_exception(e)
        await service.log(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=outcome,
            metadata={
                **(metadata or {}),
                **extra_metadata,
                "error_type": type(e).__name__,
                "error_message": str(e)[:200],  # Truncate for safety
            },
            institution_id=institution_id,
            request_id=request_id,
        )
        raise  # Re-raise the original exception


def _classify_exception(e: Exception) -> AuditOutcome:
    """Classify an exception into an audit outcome."""
    from fastapi import HTTPException
    
    if isinstance(e, HTTPException):
        if e.status_code == 401 or e.status_code == 403:
            return AuditOutcome.FAILURE_UNAUTHORIZED
        elif e.status_code == 404:
            return AuditOutcome.FAILURE_NOT_FOUND
        elif e.status_code == 400 or e.status_code == 422:
            return AuditOutcome.FAILURE_VALIDATION
    
    # Check for common exception patterns
    error_name = type(e).__name__.lower()
    if "notfound" in error_name:
        return AuditOutcome.FAILURE_NOT_FOUND
    elif "unauthorized" in error_name or "forbidden" in error_name:
        return AuditOutcome.FAILURE_UNAUTHORIZED
    elif "validation" in error_name:
        return AuditOutcome.FAILURE_VALIDATION
    
    return AuditOutcome.FAILURE_INTERNAL


# =============================================================================
# Global Service Instance (Singleton for convenience)
# =============================================================================

_audit_service: AuditService | None = None


def get_audit_service() -> AuditService:
    """
    Get the global audit service instance.
    
    Lazy initialization with PostgreSQL repository.
    """
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService(PostgresAuditRepository())
    return _audit_service


def set_audit_service(service: AuditService) -> None:
    """
    Set the global audit service instance.
    
    Useful for testing with InMemoryAuditRepository.
    """
    global _audit_service
    _audit_service = service


# =============================================================================
# Convenience Functions
# =============================================================================

async def log_audit(
    actor: AuditActor | str,
    action: AuditAction | str,
    target_resource: str,
    outcome: AuditOutcome | str,
    metadata: dict[str, Any] | None = None,
    institution_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """
    Convenience function for logging audit entries.
    
    Uses the global audit service instance.
    """
    service = get_audit_service()
    await service.log(
        actor=actor,
        action=action,
        target_resource=target_resource,
        outcome=outcome,
        metadata=metadata,
        institution_id=institution_id,
        request_id=request_id,
    )


def log_audit_background(
    actor: AuditActor | str,
    action: AuditAction | str,
    target_resource: str,
    outcome: AuditOutcome | str,
    metadata: dict[str, Any] | None = None,
    institution_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """
    Convenience function for non-blocking audit logging.
    
    Uses the global audit service instance.
    """
    service = get_audit_service()
    service.log_background(
        actor=actor,
        action=action,
        target_resource=target_resource,
        outcome=outcome,
        metadata=metadata,
        institution_id=institution_id,
        request_id=request_id,
    )
