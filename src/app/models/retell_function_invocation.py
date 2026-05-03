"""Idempotency model for Retell function-call processing.

Mid-call function invocations (book/cancel/reschedule/create_patient) are
deduped by (call_id, function_name, args_hash). The cached result is
replayed verbatim so a Retell retry produces no second side effect.

The cached result may contain PHI (appointment_id, patient_id) but mirrors
data already persisted in `appointments`/`audit_logs`/`calls` — it is not
a new disclosure surface, only an additional storage location. Cleanup is
operational; consider periodic prune of rows older than 30 days.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class RetellFunctionStatus(str, Enum):
    """Lifecycle status of a Retell function invocation record."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RetellFunctionInvocation(Base):
    """Tracks function-call processing state by (call_id, function_name, args_hash)."""

    __tablename__ = "retell_function_invocations"
    __table_args__ = (
        UniqueConstraint(
            "call_id",
            "function_name",
            "args_hash",
            name="uq_retell_function_invocation",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RetellFunctionStatus.PROCESSING.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    institution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, index=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<RetellFunctionInvocation(call_id={self.call_id}, "
            f"function_name={self.function_name}, status={self.status}, "
            f"attempts={self.attempts})>"
        )
