"""Diagnosis model — persists both stages of the crop-doctor pipeline.

Persisted pipeline fields (spec):
    crop_id, crop_confidence, crop_source, disease_id, disease_confidence
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
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


class DiagnosisStatus(str, Enum):
    """Lifecycle of a diagnosis record."""

    #: Stage 1 returned several crop candidates — the farmer must choose one.
    AWAITING_CROP_SELECTION = "awaiting_crop_selection"
    #: Created offline; queued on the server until the image is uploaded.
    QUEUED = "queued"
    #: Stage 2 confidence in [0.50, 0.75) → agronomist review queue.
    IN_REVIEW = "in_review"
    #: Stage 2 confidence >= 0.75 → confirmed diagnosis + treatment plan.
    CONFIRMED = "confirmed"
    #: Image too blurry or disease confidence < 0.50 → ask the farmer to retake.
    RETAKE_REQUIRED = "retake_required"
    #: An agronomist rejected the sample.
    REJECTED = "rejected"

    @classmethod
    def reviewable(cls) -> set[str]:
        """Statuses that belong in the agronomist review queue."""
        return {cls.IN_REVIEW.value}

    @classmethod
    def protected_from_client_downgrade(cls) -> set[str]:
        """Statuses an offline client may never reset back to ``queued``."""
        return {
            cls.IN_REVIEW.value,
            cls.CONFIRMED.value,
            cls.REJECTED.value,
        }


class CropSource(str, Enum):
    """How the crop identity of a diagnosis was established."""

    #: Stage-1 confidence >= CROP_AUTO_THRESHOLD → routed automatically.
    MODEL_AUTO = "model_auto"
    #: Stage-1 confidence < threshold → the farmer picked from the top-3.
    USER_SELECTED = "user_selected"
    #: Confidence too low: candidates returned, top-1 stored provisionally.
    MODEL_CANDIDATES = "model_candidates"


class Diagnosis(Base):
    """A single crop-doctor analysis (stage results + agronomist review)."""

    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    farmer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plots.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Stage 1: crop classification ---------------------------------
    crop_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    crop_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    crop_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    crop_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    crop_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- Stage 2: disease identification ------------------------------
    disease_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    disease_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    disease_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    treatment_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Workflow ------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DiagnosisStatus.AWAITING_CROP_SELECTION.value, index=True
    )
    stage: Mapped[int] = mapped_column(nullable=False, default=1)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Agronomist review --------------------------------------------
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Sync metadata -------------------------------------------------
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

    plot = relationship("Plot")

    @property
    def source(self) -> str:
        """Result provenance exposed to clients.

        ``local_cv``     — local OpenCV/NumPy engine answered, always the
                           case for records created by the v4 pipeline,
        ``mock``         — legacy historical rows only; the mock engine
                           was removed — failures now answer 503 instead,
        ``quality_gate`` — local image-quality checks only (retake path).
        """
        meta = self.model_meta
        if isinstance(meta, dict):
            return str(meta.get("source") or "local_cv")
        return "local_cv"

    __table_args__ = (
        UniqueConstraint("farmer_id", "client_id", name="uq_diagnoses_farmer_id_client_id"),
        Index("ix_diagnoses_farmer_id_updated_at", "farmer_id", "updated_at"),
        Index("ix_diagnoses_status_created_at", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Diagnosis {self.id} status={self.status} crop={self.crop_id}>"
