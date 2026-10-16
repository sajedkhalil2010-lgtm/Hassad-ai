"""User model and role-based access control definitions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, new_id, utcnow


class UserRole(str, Enum):
    """Role-Based Access Control roles supported by the API."""

    FARMER = "farmer"
    AGRONOMIST = "agronomist"
    ADMIN = "admin"

    @classmethod
    def public_choices(cls) -> set[str]:
        """Roles that anybody may self-register with (admin is seeded only)."""
        return {cls.FARMER.value, cls.AGRONOMIST.value}


class User(Base):
    """An authenticated actor: farmer, agronomist or admin."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.FARMER.value, index=True
    )
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="ar")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    farms = relationship("Farm", back_populates="owner", cascade="all, delete-orphan")

    # ------------------------------------------------------------------
    @property
    def role_enum(self) -> UserRole:
        """Typed view over the persisted role string."""
        try:
            return UserRole(self.role)
        except ValueError:
            return UserRole.FARMER

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User {self.email} role={self.role}>"
