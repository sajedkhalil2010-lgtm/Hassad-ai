"""Schemas for the two-stage crop-doctor pipeline, the review queue and the
RAG chatbot.

(Chatbot schemas live in this domain module — the project's schema layer is
organised as ``auth / diagnosis / sync``.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.diagnosis import DiagnosisStatus

# ======================================================================
# Shared value objects
# ======================================================================


class LocalizedText(BaseModel):
    """Bilingual (Arabic-first) text block."""

    en: str = ""
    ar: str = ""


class CropCandidate(BaseModel):
    """One candidate crop returned by Stage 1."""

    id: str = Field(description="Stable slug, e.g. 'tomato'")
    label: str = Field(description="Human readable label as produced by the model")
    confidence: float = Field(ge=0.0, le=1.0)


class ImageQualityReport(BaseModel):
    """Result of the Stage-1 blur / size validation."""

    width: int
    height: int
    format: str = ""
    bytes_size: int
    blur_variance: float
    is_blurry: bool
    checks: dict[str, bool] = Field(default_factory=dict)


class ControlOption(BaseModel):
    type: Literal["cultural", "organic", "chemical", "biological"] = "cultural"
    action: LocalizedText
    notes: LocalizedText | None = None


class TreatmentPlan(BaseModel):
    """Confirmed treatment plan returned with a confirmed diagnosis."""

    summary: LocalizedText
    immediate_actions: list[LocalizedText] = Field(default_factory=list)
    control_options: list[ControlOption] = Field(default_factory=list)
    monitoring: list[LocalizedText] = Field(default_factory=list)
    prevention: list[LocalizedText] = Field(default_factory=list)
    #: Observed symptoms extracted by the vision model (Arabic-first).
    symptoms: list[LocalizedText] = Field(default_factory=list)
    disclaimer: LocalizedText
    source: str = "hassad_knowledge_base"


# ======================================================================
# Crop doctor
# ======================================================================


class DiagnosisResponse(BaseModel):
    """Uniform response for both pipeline stages.

    * ``stage == 1`` + ``requires_crop_selection == True``  → farmer picks a crop
      from ``candidates`` and re-posts the image with ``selected_crop``.
    * ``stage == 2`` → ``status`` tells the client what happened:
      ``confirmed`` / ``in_review`` / ``retake_required``.
    """

    diagnosis_id: str
    stage: int = Field(ge=1, le=2)
    status: DiagnosisStatus
    requires_crop_selection: bool = False
    message: str = ""
    #: "model" = live Gemini inference · "quality_gate" · "mock" = legacy rows only
    source: str = "model"

    crop_id: str | None = None
    crop_label: str | None = None
    crop_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    crop_source: str | None = None
    candidates: list[CropCandidate] = Field(default_factory=list)

    disease_id: str | None = None
    disease_label: str | None = None
    disease_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    treatment_plan: TreatmentPlan | None = None

    image_quality: ImageQualityReport | None = None

    @classmethod
    def from_diagnosis(
        cls,
        diagnosis,  # app.models.diagnosis.Diagnosis
        *,
        candidates: list[CropCandidate] | None = None,
        requires_crop_selection: bool = False,
        message: str = "",
        image_quality: ImageQualityReport | None = None,
        treatment_plan: TreatmentPlan | None = None,
    ) -> "DiagnosisResponse":
        stored_candidates = candidates
        if stored_candidates is None and diagnosis.crop_candidates:
            stored_candidates = [CropCandidate.model_validate(c) for c in diagnosis.crop_candidates]

        plan = treatment_plan
        if plan is None and diagnosis.treatment_plan:
            plan = TreatmentPlan.model_validate(diagnosis.treatment_plan)

        return cls(
            diagnosis_id=diagnosis.id,
            stage=diagnosis.stage or 1,
            status=DiagnosisStatus(diagnosis.status),
            requires_crop_selection=requires_crop_selection,
            message=message,
            source=getattr(diagnosis, "source", None) or "model",
            crop_id=diagnosis.crop_id,
            crop_label=diagnosis.crop_label,
            crop_confidence=diagnosis.crop_confidence,
            crop_source=diagnosis.crop_source,
            candidates=stored_candidates or [],
            disease_id=diagnosis.disease_id,
            disease_label=diagnosis.disease_label,
            disease_confidence=diagnosis.disease_confidence,
            treatment_plan=plan,
            image_quality=image_quality,
        )


class DiagnosisOut(BaseModel):
    """Full diagnosis record (list / detail endpoints)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str | None = None
    farmer_id: str
    plot_id: str | None = None
    #: "model" | "quality_gate" | "mock" (legacy rows) — read from model_meta
    source: str = "model"

    crop_id: str | None = None
    crop_label: str | None = None
    crop_confidence: float | None = None
    crop_source: str | None = None
    crop_candidates: list[CropCandidate] | None = None

    disease_id: str | None = None
    disease_label: str | None = None
    disease_confidence: float | None = None
    treatment_plan: TreatmentPlan | None = None

    status: DiagnosisStatus
    stage: int
    notes: str = ""
    model_meta: dict | None = None

    review_notes: str = ""
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    created_at: datetime
    updated_at: datetime


class DiagnosisListResponse(BaseModel):
    items: list[DiagnosisOut]
    total: int
    limit: int
    offset: int


# ======================================================================
# Agronomist review queue
# ======================================================================


class ReviewDecisionRequest(BaseModel):
    action: Literal["confirm", "reject", "retake"] = "confirm"
    notes: str = Field(default="", max_length=4000)
    treatment_plan: TreatmentPlan | None = Field(
        default=None, description="Optional agronomist-authored plan (action=confirm)"
    )

    @field_validator("notes", mode="after")
    @classmethod
    def _strip_notes(cls, value: str) -> str:
        return (value or "").strip()


class ReviewQueueResponse(BaseModel):
    items: list[DiagnosisOut]
    total: int
    limit: int
    offset: int


class AgronomistStats(BaseModel):
    queued: int = 0
    in_review: int = 0
    confirmed: int = 0
    retake_required: int = 0
    rejected: int = 0
    awaiting_crop_selection: int = 0
    total: int = 0


# ======================================================================
# Chatbot
# ======================================================================


class ChatSessionCreate(BaseModel):
    client_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="New conversation", max_length=160)
    plot_id: str | None = None
    locale: str = Field(default="ar", pattern="^(ar|en)$")
    crop_context: dict | None = Field(
        default=None, description="Free-form context merged into the RAG prompt"
    )


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str | None = None
    title: str
    plot_id: str | None = None
    locale: str
    crop_context: dict | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ChatMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    lang: str = Field(default="auto", pattern="^(auto|ar|en)$")

    @field_validator("content", mode="after")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be empty")
        return cleaned


class CitationCard(BaseModel):
    """A knowledge-base citation card returned alongside the answer."""

    index: int = Field(description="1-based reference used by [n] markers")
    title: str
    source: str
    source_url: str = ""
    category: str = "general"
    lang: str = "both"
    snippet: str = ""
    cited: bool = False


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    citations: list[CitationCard] | None = None
    created_at: datetime


class ChatAskRequest(BaseModel):
    """One-shot (non-streaming) question — handy for simple clients & tests."""

    question: str = Field(min_length=1, max_length=4000)
    plot_id: str | None = None
    session_id: str | None = None
    lang: str = Field(default="auto", pattern="^(auto|ar|en)$")

    @field_validator("question", mode="after")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        return cleaned


class ChatAskResponse(BaseModel):
    answer: str
    citations: list[CitationCard] = Field(default_factory=list)
    mode: Literal["gemini", "retrieval_fallback"] = "gemini"
    session_id: str | None = None
    notice: str | None = None
