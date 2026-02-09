"""
Audit logging model for HIPAA compliance.

SOLID Principles Applied:
- SRP: Model only handles data representation and validation
- OCP: Enums are extensible without modifying existing code
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class AuditActor(str, Enum):
    """
    Who performed the action.
    
    Extensible: Add new actors without modifying existing code (OCP).
    """
    GHL = "GHL"                     # GoHighLevel webhook
    RETELL_AGENT = "RETELL_AGENT"   # Retell Voice Agent
    ADMIN = "ADMIN"                 # Admin API user
    SYSTEM = "SYSTEM"               # Internal system operations
    API_CLIENT = "API_CLIENT"       # External API client


class AuditAction(str, Enum):
    """
    What action was performed on PHI.
    
    Extensible: Add new actions without modifying existing code (OCP).
    """
    # Patient operations
    READ_PATIENT = "READ_PATIENT"
    CREATE_PATIENT = "CREATE_PATIENT"
    UPDATE_PATIENT = "UPDATE_PATIENT"
    SEARCH_PATIENTS = "SEARCH_PATIENTS"
    
    # Appointment operations
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    READ_APPOINTMENT = "READ_APPOINTMENT"
    
    # Resource listing (may expose PHI indirectly)
    READ_PROVIDERS = "READ_PROVIDERS"
    READ_APPOINTMENT_SLOTS = "READ_APPOINTMENT_SLOTS"
    READ_LOCATIONS = "READ_LOCATIONS"
    READ_APPOINTMENT_TYPES = "READ_APPOINTMENT_TYPES"
    
    # External Integrations
    WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"
    SYNC_GHL_CONTACT = "SYNC_GHL_CONTACT"
    
    # Admin operations
    TENANT_CREATE = "TENANT_CREATE"
    TENANT_UPDATE = "TENANT_UPDATE"
    TENANT_DELETE = "TENANT_DELETE"
    LOCATION_CREATE = "LOCATION_CREATE"
    LOCATION_UPDATE = "LOCATION_UPDATE"
    LOCATION_DELETE = "LOCATION_DELETE"
    LOCATION_SYNC = "LOCATION_SYNC"
    
    # Auth operations
    LOGIN = "LOGIN"


class AuditOutcome(str, Enum):
    """
    Result of the action.
    
    Extensible: Add new outcomes without modifying existing code (OCP).
    """
    SUCCESS = "SUCCESS"
    FAILURE_UNAUTHORIZED = "FAILURE_UNAUTHORIZED"
    FAILURE_NOT_FOUND = "FAILURE_NOT_FOUND"
    FAILURE_VALIDATION = "FAILURE_VALIDATION"
    FAILURE_EXTERNAL_API = "FAILURE_EXTERNAL_API"
    FAILURE_INTERNAL = "FAILURE_INTERNAL"


class AuditLog(Base):
    """
    Immutable audit log entry for HIPAA compliance.
    
    IMPORTANT: This table should be append-only. No updates or deletes.
    
    Fields match the architecture document:
    - timestamp: When the action occurred (UTC)
    - actor: Who performed it (GHL, RETELL Agent, Admin, System)
    - action: What was done (READ_PATIENT, BOOK_APPOINTMENT, etc.)
    - target_resource: What resource was accessed
    - outcome: Success or type of failure
    - metadata: Additional context (request_id, ip_address, etc.)
    
    SRP: Only responsible for data representation.
    """
    
    __tablename__ = "audit_logs"
    
    # Primary key - UUID for distributed systems compatibility
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    
    # When the action occurred (UTC, immutable)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True  # For time-range queries
    )
    
    # Who performed the action
    actor: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True  # For filtering by actor
    )
    
    # What action was performed
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True  # For filtering by action type
    )
    
    # What resource was accessed (e.g., "patient:123", "appointment:456")
    target_resource: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    
    # Result of the action
    outcome: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True  # For finding failures
    )
    
    # Additional context (NO PHI should be stored here)
    # Example: {"request_id": "...", "ip_address": "...", "tenant_id": "..."}
    audit_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True
    )
    
    # Optional: Tenant association for multi-tenant filtering
    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
        index=True  # For tenant-scoped queries
    )
    
    def __repr__(self) -> str:
        return (
            f"<AuditLog("
            f"id={self.id}, "
            f"actor={self.actor}, "
            f"action={self.action}, "
            f"outcome={self.outcome}"
            f")>"
        )
    
    @classmethod
    def create(
        cls,
        actor: AuditActor | str,
        action: AuditAction | str,
        target_resource: str,
        outcome: AuditOutcome | str,
        audit_metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> "AuditLog":
        """
        Factory method for creating audit log entries.
        
        Accepts both Enum values and strings for flexibility.
        """
        return cls(
            actor=actor.value if isinstance(actor, AuditActor) else actor,
            action=action.value if isinstance(action, AuditAction) else action,
            target_resource=target_resource,
            outcome=outcome.value if isinstance(outcome, AuditOutcome) else outcome,
            audit_metadata=audit_metadata,
            tenant_id=tenant_id,
        )
