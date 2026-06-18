"""Workflow Status: human-assigned, tenant-defined workflow state for calls.

Distinct from the AI-assigned classification (``call_status`` / ``call_tags``):
those describe *what the call was about*; a Workflow Status is what the *team*
needs to do or has done (Pending, Completed, Not Completed, Reviewed, …).

Design (see also the custom_field definition precedent):
- Definitions are **institution-scoped** (one shared vocabulary per clinic, so
  cross-location/clinic reporting stays consistent), managed by INSTITUTION_ADMIN
  or LOCATION_ADMIN.
- A call references at most one status via ``calls.workflow_status_id`` (single
  select). Filtering is indexed FK equality — fast at scale, never text matching.
- Renaming/recoloring is a one-row update; assignments keep their FK (no backfill).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


# Allowed palette keys. Must stay in sync with the frontend color map
# (nexus-dashboard-web/src/lib/status-colors.ts). Storing a palette key rather
# than raw hex keeps badges on-theme (light/dark) and avoids Tailwind purging
# dynamically-built class strings.
WORKFLOW_STATUS_COLORS: tuple[str, ...] = (
    "zinc", "slate", "red", "orange", "amber", "yellow", "lime", "green",
    "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "fuchsia",
    "pink", "rose",
)

# Soft cap on *active* statuses per institution. The cap is itself a scale
# feature: it keeps the filter UI usable and prevents vocabulary fragmentation
# (15 near-duplicate "Done" variants) that would wreck cross-tenant analytics.
# Archive (is_active=False) to free a slot.
MAX_ACTIVE_WORKFLOW_STATUSES = 20

# Seeded for every institution so the feature works out of the box and gives a
# consistent baseline across clinics. (name, color, display_order)
DEFAULT_WORKFLOW_STATUSES: tuple[tuple[str, str, int], ...] = (
    ("Pending", "amber", 0),
    ("In Progress", "blue", 1),
    ("Completed", "emerald", 2),
    ("Not Completed", "rose", 3),
    ("Reviewed", "violet", 4),
)


class WorkflowStatus(Base):
    """A tenant-defined workflow status that staff/admins assign to a call."""

    __tablename__ = "workflow_statuses"
    __table_args__ = (
        UniqueConstraint("institution_id", "name", name="uq_workflow_status_institution_name"),
        Index("ix_workflow_status_institution_order", "institution_id", "display_order"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Display name shown in the UI (e.g. "Not Completed").
    name: Mapped[str] = mapped_column(String(60), nullable=False)

    # Palette key (see WORKFLOW_STATUS_COLORS), not a hex value.
    color: Mapped[str] = mapped_column(String(20), default="zinc", nullable=False)

    # UI ordering.
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Soft delete — archived statuses keep historical call assignments valid.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<WorkflowStatus(id={self.id}, name='{self.name}', color={self.color})>"
