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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Protocol, runtime_checkable
from uuid import UUID, uuid4

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome
from src.app.services.sms_privacy import hash_for_logging, safe_error_summary

logger = logging.getLogger(__name__)


class AuditPersistenceError(RuntimeError):
    """Raised when an audit row cannot be durably persisted.

    Callers performing PHI-touching actions MUST treat this as fatal — the
    row is the legal record of access, and a missing row leaves PHI
    activity unattributed.
    """


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
    user_id: str | None = None
    location_id: str | None = None
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


async def _resolve_jurisdiction(session: Any, institution_id: str | None) -> str | None:
    """Look up an institution's jurisdiction for residency-of-record stamping."""
    if not institution_id:
        return None
    from sqlalchemy import select
    from src.app.models.institution import Institution

    result = await session.execute(
        select(Institution.jurisdiction).where(Institution.id == institution_id)
    )
    return result.scalar_one_or_none()


class PostgresAuditRepository:
    """
    PostgreSQL implementation of audit repository.

    LSP: Can substitute for IAuditRepository in any context.
    SRP: Only responsible for database persistence.
    """

    async def save(self, entry: AuditEntry) -> None:
        """Persist a single audit entry to PostgreSQL."""
        from src.app.database import get_system_db_session

        async with get_system_db_session(
            "audit",
            institution_id=entry.institution_id,
            location_id=entry.location_id,
            user_id=entry.user_id,
        ) as session:
            metadata = {"request_id": entry.request_id, **entry.metadata}
            if "jurisdiction" not in metadata:
                jurisdiction = await _resolve_jurisdiction(
                    session, entry.institution_id
                )
                if jurisdiction:
                    metadata["jurisdiction"] = jurisdiction

            audit_log = AuditLog.create(
                actor=entry.actor,
                action=entry.action,
                target_resource=entry.target_resource,
                outcome=entry.outcome,
                audit_metadata=metadata,
                institution_id=entry.institution_id,
                user_id=entry.user_id,
                location_id=entry.location_id,
            )
            # Override timestamp if provided
            audit_log.timestamp = entry.timestamp
            session.add(audit_log)
            # Commit happens automatically on context exit

    async def save_batch(self, entries: list[AuditEntry]) -> None:
        """Persist multiple audit entries atomically."""
        from src.app.database import get_system_db_session

        first = entries[0] if entries else None
        async with get_system_db_session(
            "audit",
            institution_id=first.institution_id if first else None,
            location_id=first.location_id if first else None,
            user_id=first.user_id if first else None,
        ) as session:
            jurisdiction_cache: dict[str, str | None] = {}
            for entry in entries:
                metadata = {"request_id": entry.request_id, **entry.metadata}
                if "jurisdiction" not in metadata and entry.institution_id:
                    if entry.institution_id not in jurisdiction_cache:
                        jurisdiction_cache[
                            entry.institution_id
                        ] = await _resolve_jurisdiction(session, entry.institution_id)
                    cached = jurisdiction_cache[entry.institution_id]
                    if cached:
                        metadata["jurisdiction"] = cached

                audit_log = AuditLog.create(
                    actor=entry.actor,
                    action=entry.action,
                    target_resource=entry.target_resource,
                    outcome=entry.outcome,
                    audit_metadata=metadata,
                    institution_id=entry.institution_id,
                    user_id=entry.user_id,
                    location_id=entry.location_id,
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
        user_id: str | None = None,
        location_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Persist an audit entry. Raises ``AuditPersistenceError`` on failure.

        This is the synchronous, durable path — callers MUST handle or
        propagate the exception. PHI access without a persisted audit row is
        a HIPAA-relevant gap, so silent swallowing is a defect.

        For best-effort logging where dropping a row is acceptable, use
        ``log_background`` instead.
        """
        entry = AuditEntry(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=outcome,
            metadata=metadata or {},
            institution_id=institution_id,
            user_id=user_id
            or _coerce_uuid(
                (metadata or {}).get("actor_user_id")
                or (metadata or {}).get("user_id")
                or actor
            ),
            location_id=location_id
            or _coerce_uuid((metadata or {}).get("location_id")),
            request_id=request_id or str(uuid4()),
        )

        try:
            await self._repository.save(entry)
        except Exception as e:
            # Surface as a typed error so callers can distinguish persistence
            # failure from any business-logic exception.
            logger.critical(
                "AUDIT WRITE FAILURE action=%s outcome=%s institution_hash=%s "
                "resource_hash=%s request_id=%s error_type=%s",
                action,
                outcome,
                hash_for_logging(institution_id),
                hash_for_logging(target_resource),
                entry.request_id,
                type(e).__name__,
            )
            raise AuditPersistenceError(
                f"Failed to persist audit row for {action}"
            ) from e

        logger.debug(
            "Audit logged: action=%s resource_hash=%s actor=%s outcome=%s",
            action,
            hash_for_logging(target_resource),
            actor,
            outcome,
        )

    # Strong references to background tasks so the event loop's GC cannot
    # collect them mid-run (asyncio's create_task only keeps a weakref).
    _background_tasks: set[asyncio.Task] = set()

    @classmethod
    async def drain_background_tasks(cls, *, timeout_seconds: float = 10.0) -> int:
        """Wait for in-flight best-effort audit writes to finish.

        Called from the FastAPI lifespan shutdown branch so SIGTERM /
        rolling-deploy events don't drop pending audit rows for actions that
        already happened (login, dashboard view, callback resolve, etc).

        Returns the number of tasks that were pending when called. Bounded
        by ``timeout_seconds`` so a wedged audit DB cannot block shutdown
        indefinitely.
        """
        pending = [t for t in cls._background_tasks if not t.done()]
        if not pending:
            return 0
        logger.info(
            "Draining %d background audit task(s) before shutdown", len(pending)
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            still_pending = sum(1 for t in pending if not t.done())
            logger.error(
                "Background audit drain timed out: %d task(s) still pending. "
                "Audit rows for in-flight actions may be lost.",
                still_pending,
            )
        return len(pending)

    def log_background(
        self,
        actor: AuditActor | str,
        action: AuditAction | str,
        target_resource: str,
        outcome: AuditOutcome | str,
        metadata: dict[str, Any] | None = None,
        institution_id: str | None = None,
        user_id: str | None = None,
        location_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Best-effort, non-blocking audit log.

        All identifying values (actor, institution_id, user_id, location_id,
        request_id) are captured by the closure *now*, so the eventual log
        row reflects the caller's context even if the task runs after the
        caller's contextvars have been reset.

        Failures are logged at ERROR level but never raised. Use this only
        for non-mutating reads where a dropped row is tolerable.
        """
        # Snapshot all parameters explicitly so the closure cannot pick up
        # later mutations to a shared dict.
        snapshot_metadata = dict(metadata) if metadata else None

        async def _safe_log() -> None:
            try:
                await self.log(
                    actor=actor,
                    action=action,
                    target_resource=target_resource,
                    outcome=outcome,
                    metadata=snapshot_metadata,
                    institution_id=institution_id,
                    user_id=user_id,
                    location_id=location_id,
                    request_id=request_id,
                )
            except Exception as e:
                logger.error(
                    "Background audit log failed action=%s outcome=%s "
                    "institution_hash=%s error=%s",
                    action,
                    outcome,
                    hash_for_logging(institution_id),
                    safe_error_summary(e),
                )

        try:
            task = asyncio.create_task(_safe_log())
        except RuntimeError:
            # No running event loop (e.g. inside a sync Celery task).
            # Run synchronously to ensure the row is still attempted.
            asyncio.run(_safe_log())
            return

        type(self)._background_tasks.add(task)
        task.add_done_callback(type(self)._background_tasks.discard)


# =============================================================================
# Audit Context Manager (OCP)
# =============================================================================


@asynccontextmanager
async def phi_reveal_audit(
    *,
    actor: AuditActor | str,
    action: AuditAction | str,
    target_resource: str,
    institution_id: str | None,
    user_id: str | None,
    location_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[None, None]:
    """Two-row pre-then-post audit pattern for PHI-reveal endpoints.

    Writes an ``INITIATED`` row BEFORE yielding control. If that write
    fails the body never runs — no PHI is decrypted or returned. On
    normal completion writes a paired ``SUCCESS`` row; on exception
    writes a ``FAILURE_INTERNAL`` row and re-raises. Both pre/post rows
    share one ``request_id`` so an "intent without completion"
    reconciliation report can find orphans if the post-write fails.
    """
    request_id = str(uuid4())
    base_metadata = dict(metadata or {})

    await log_audit(
        actor=actor,
        action=action,
        target_resource=target_resource,
        outcome=AuditOutcome.INITIATED,
        metadata={**base_metadata, "phase": "intent"},
        institution_id=institution_id,
        user_id=user_id,
        location_id=location_id,
        request_id=request_id,
    )

    try:
        yield
    except Exception as e:
        await log_audit(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=AuditOutcome.FAILURE_INTERNAL,
            metadata={
                **base_metadata,
                "phase": "complete",
                "error_type": type(e).__name__,
            },
            institution_id=institution_id,
            user_id=user_id,
            location_id=location_id,
            request_id=request_id,
        )
        raise

    await log_audit(
        actor=actor,
        action=action,
        target_resource=target_resource,
        outcome=AuditOutcome.SUCCESS,
        metadata={**base_metadata, "phase": "complete"},
        institution_id=institution_id,
        user_id=user_id,
        location_id=location_id,
        request_id=request_id,
    )


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
        # Truncating str(e) is NOT de-identification: a 200-char prefix of
        # a vendor exception still contains patient name / DOB / phone.
        # Persist only the structural fields — type, HTTP status, and any
        # structured error code the exception exposes. The audit_metadata
        # JSONB column docs explicitly forbid PHI here.
        await service.log(
            actor=actor,
            action=action,
            target_resource=target_resource,
            outcome=outcome,
            metadata={
                **(metadata or {}),
                **extra_metadata,
                "error_type": type(e).__name__,
                "error_summary": safe_error_summary(e),
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


def _coerce_uuid(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        return str(UUID(text))
    except (TypeError, ValueError, AttributeError):
        return None


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
    user_id: str | None = None,
    location_id: str | None = None,
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
        user_id=user_id,
        location_id=location_id,
        request_id=request_id,
    )


def log_audit_background(
    actor: AuditActor | str,
    action: AuditAction | str,
    target_resource: str,
    outcome: AuditOutcome | str,
    metadata: dict[str, Any] | None = None,
    institution_id: str | None = None,
    user_id: str | None = None,
    location_id: str | None = None,
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
        user_id=user_id,
        location_id=location_id,
        request_id=request_id,
    )
