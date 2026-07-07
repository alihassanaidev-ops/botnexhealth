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

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class AuditActor(str, Enum):
    """
    Who performed the action.

    Extensible: Add new actors without modifying existing code (OCP).
    """
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
    CONFIRM_APPOINTMENT = "CONFIRM_APPOINTMENT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    READ_APPOINTMENT = "READ_APPOINTMENT"

    # Resource listing (may expose PHI indirectly)
    READ_PROVIDERS = "READ_PROVIDERS"
    READ_APPOINTMENT_SLOTS = "READ_APPOINTMENT_SLOTS"
    READ_LOCATIONS = "READ_LOCATIONS"
    READ_APPOINTMENT_TYPES = "READ_APPOINTMENT_TYPES"
    VIEW_CALLS = "VIEW_CALLS"
    VIEW_CALL_DETAIL = "VIEW_CALL_DETAIL"
    VIEW_FULL_TRANSCRIPT = "VIEW_FULL_TRANSCRIPT"
    VIEW_CALL_RECORDING = "VIEW_CALL_RECORDING"
    VIEW_CUSTOM_PHI_FIELD = "VIEW_CUSTOM_PHI_FIELD"
    VIEW_DASHBOARD = "VIEW_DASHBOARD"
    VIEW_AUDIT_LOGS = "VIEW_AUDIT_LOGS"

    # External Integrations
    WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"
    SMS_SEND = "SMS_SEND"
    SMS_SUPPRESSION_CREATE = "SMS_SUPPRESSION_CREATE"
    SMS_SUPPRESSION_RELEASE = "SMS_SUPPRESSION_RELEASE"
    # Channel-agnostic do-not-contact (staff-initiated, privileged). Distinct
    # from SMS suppression: a DNC blocks every channel for its scope tier.
    DO_NOT_CONTACT_CREATE = "DO_NOT_CONTACT_CREATE"
    DO_NOT_CONTACT_RELEASE = "DO_NOT_CONTACT_RELEASE"
    VIEW_FULL_PHONE = "VIEW_FULL_PHONE"
    VIEW_SMS_BODY = "VIEW_SMS_BODY"
    DEAD_LETTER_REPLAY = "DEAD_LETTER_REPLAY"
    DEAD_LETTER_DISCARD = "DEAD_LETTER_DISCARD"


    # Admin operations
    INSTITUTION_CREATE = "INSTITUTION_CREATE"
    INSTITUTION_UPDATE = "INSTITUTION_UPDATE"
    INSTITUTION_DELETE = "INSTITUTION_DELETE"
    LOCATION_CREATE = "LOCATION_CREATE"
    LOCATION_UPDATE = "LOCATION_UPDATE"
    LOCATION_DELETE = "LOCATION_DELETE"
    LOCATION_SYNC = "LOCATION_SYNC"
    LOCATION_USER_CREATE = "LOCATION_USER_CREATE"
    LOCATION_USER_DELETE = "LOCATION_USER_DELETE"
    USER_DELETE = "USER_DELETE"
    CONTACT_MERGE = "CONTACT_MERGE"
    CONTACT_UNMERGE = "CONTACT_UNMERGE"
    GROUP_CREATE = "GROUP_CREATE"
    GROUP_ASSIGN = "GROUP_ASSIGN"
    GROUP_UNASSIGN = "GROUP_UNASSIGN"
    EXTERNAL_RECIPIENT_ADD = "EXTERNAL_RECIPIENT_ADD"
    EXTERNAL_RECIPIENT_UPDATE = "EXTERNAL_RECIPIENT_UPDATE"
    EXTERNAL_RECIPIENT_REMOVE = "EXTERNAL_RECIPIENT_REMOVE"

    # Auth operations
    LOGIN = "LOGIN"
    PASSWORD_SET = "PASSWORD_SET"
    PASSWORD_RESET_REQUEST = "PASSWORD_RESET_REQUEST"
    PASSWORD_RESET_COMPLETE = "PASSWORD_RESET_COMPLETE"
    ACCOUNT_UNLOCK = "ACCOUNT_UNLOCK"
    USER_REINVITED = "USER_REINVITED"
    MFA_CHALLENGE = "MFA_CHALLENGE"
    MFA_ENROLL = "MFA_ENROLL"
    MFA_VERIFY = "MFA_VERIFY"
    MFA_RECOVERY_CODE_USE = "MFA_RECOVERY_CODE_USE"
    MFA_RECOVERY_CODES_REGENERATE = "MFA_RECOVERY_CODES_REGENERATE"
    MFA_FACTOR_REMOVE = "MFA_FACTOR_REMOVE"
    MFA_FACTOR_DISABLE = "MFA_FACTOR_DISABLE"


class AuditOutcome(str, Enum):
    """
    Result of the action.

    INITIATED is written BEFORE a side-effecting durable action runs. If the
    side effect (e.g. booking via NexHealth) succeeds but the post-action
    audit write fails, the INITIATED row is the breadcrumb operators use to
    reconcile. Compliance reports filtering on "completed actions" should
    exclude INITIATED.

    Extensible: Add new outcomes without modifying existing code (OCP).
    """
    INITIATED = "INITIATED"
    SUCCESS = "SUCCESS"
    FAILURE_UNAUTHORIZED = "FAILURE_UNAUTHORIZED"
    FAILURE_NOT_FOUND = "FAILURE_NOT_FOUND"
    FAILURE_VALIDATION = "FAILURE_VALIDATION"
    FAILURE_EXTERNAL_API = "FAILURE_EXTERNAL_API"
    FAILURE_INTERNAL = "FAILURE_INTERNAL"
    FAILURE_ACCOUNT_LOCKED = "FAILURE_ACCOUNT_LOCKED"


class AuditLog(Base):
    """
    Immutable audit log entry for HIPAA compliance.

    IMPORTANT: This table should be append-only. No updates or deletes.

    Fields match the architecture document:
    - timestamp: When the action occurred (UTC)
    - actor: Who performed it (RETELL Agent, Admin, System)
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
    # Example: {"request_id": "...", "ip_address": "...", "institution_id": "..."}
    audit_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True
    )

    # Optional: Institution association for multi-institution filtering
    institution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
        index=True  # For institution-scoped queries
    )

    # Optional: Acting user and location for direct filtering without JSON metadata scans.
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
        index=True,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
        index=True,
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
        institution_id: str | None = None,
        user_id: str | None = None,
        location_id: str | None = None,
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
            institution_id=institution_id,
            user_id=user_id,
            location_id=location_id,
        )
