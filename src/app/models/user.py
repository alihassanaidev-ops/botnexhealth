"""
User model for authentication and role management.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class UserRole(str, Enum):
    """
    User roles for authorization.
    """
    ADMIN = "ADMIN"
    TENANT = "TENANT"


class User(Base):
    """
    User model for authentication.
    
    Fields:
    - id: Unique identifier
    - email: User's email address (login credential)
    - hashed_password: Bcrypt hashed password
    - role: User role (ADMIN, TENANT)
    - tenant_id: Tenant this user belongs to (nullable for ADMIN)
    - is_active: Whether the user can log in
    - failed_login_attempts: For brute force protection
    - last_login_at: Timestamp of last successful login
    """
    
    __tablename__ = "users"
    
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )
    
    hashed_password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )
    
    role: Mapped[str] = mapped_column(
        String(50),
        default=UserRole.TENANT.value,
        nullable=False
    )
    
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=True,
        index=True
    )
    
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"
