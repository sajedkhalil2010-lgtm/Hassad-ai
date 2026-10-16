"""Farm & Plot models (offline-first: every row carries sync metadata).

Sync contract
-------------
* ``client_id``      — UUID generated on the device **before** the record is
  saved offline.  It is unique per owner, which makes sync idempotent
  (re-pushing the same record never creates a duplicate).
* ``client_updated_at`` — device timestamp of the last local edit; used for
  **last-write-wins** resolution.
* ``updated_at``      — server timestamp; used as the ``since`` cursor for
  incremental pulls.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, new_id, utcnow


class Farm(Base):
    """A farm owned by a user (farmer)."""

    __tablename__ = "farms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    location_text: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_hectares: Mapped[float | None] = mapped_column(Float, nullable=True)
    main_crop: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    client_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner = relationship("User", back_populates="farms")
    plots = relationship("Plot", back_populates="farm", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("owner_id", "client_id", name="uq_farms_owner_id_client_id"),
        Index("ix_farms_owner_id_updated_at", "owner_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Farm {self.name!r} owner={self.owner_id}>"


class Plot(Base):
    """A parcel/field inside a farm where crops are grown."""

    __tablename__ = "plots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    farm_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    area_hectares: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_crop: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    soil_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    irrigation_method: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    planted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_harvest_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    client_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    farm = relationship("Farm", back_populates="plots")

    __table_args__ = (
        UniqueConstraint("farm_id", "client_id", name="uq_plots_farm_id_client_id"),
        Index("ix_plots_farm_id_updated_at", "farm_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Plot {self.name!r} farm={self.farm_id}>"
