"""Crop Doctor — thin HTTP layer over the v2 multi-agent pipeline.

The workflow itself lives in the agents
(``app.agents.supervisor_agent``): the :class:`SupervisorAgent`
coordinates the DiagnosticAgent (visual pathology) and the AdvisorAgent
(treatment plan) and returns one unified JSON report. This router only
handles what routers should: uploads, validation, ownership, persistence
and the public ``DiagnosisResponse`` the (untouched) frontend consumes.

Decision policy (applied by the supervisor):

* confidence >= ``CROP_AUTO_THRESHOLD`` (0.80) → deep diagnosis continues
  automatically; below it → top-3 candidates are returned so the farmer
  can pick (``awaiting_crop_selection``).
* disease confidence >= ``DISEASE_CONFIRM_THRESHOLD`` (0.75) → confirmed
  diagnosis + localized treatment plan.
* confidence in [``DISEASE_REVIEW_THRESHOLD``, 0.75) → agronomist review
  queue (``in_review``).
* below it → the farmer is asked to retake the photo.

**Live-only contract** — no mock/simulated verdict exists. When the
model cannot answer, agents raise ``AgentFailure`` and this endpoint
answers with a structured, *user-friendly* error: **429**
(``code: vision_quota``) when the daily free-tier allowance is spent
(after automatic rotation through every key in the pool), **503**
(``code: vision_busy``) for congestion/timeouts — English-first
messages, never freezing the UI.

Every run persists ``crop_id``, ``crop_confidence``, ``crop_source``,
``disease_id`` and ``disease_confidence`` on the ``diagnoses`` table.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentContext, AgentFailure, KeyPoolManager, SupervisorAgent
from app.agents.base import raw_mime_for
from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.database import new_id, utcnow
from app.models.diagnosis import Diagnosis, DiagnosisStatus
from app.models.farm import Farm, Plot
from app.schemas.diagnosis import (
    CropCandidate,
    DiagnosisListResponse,
    DiagnosisOut,
    DiagnosisResponse,
    TreatmentPlan,
)
from app.services.ai_vision import (
    ValidatedImage,
    slugify,
    validate_crop_image,
)

logger = logging.getLogger("hassad.api.crop_doctor")

router = APIRouter(prefix="/crop-doctor", tags=["Crop Doctor"])

_STATUS_VALUES = {status_.value for status_ in DiagnosisStatus}

# ======================================================================
# Live-only honesty: when the model cannot answer inside its bounded
# budget the agents raise AgentFailure (no mock exists) and we return a
# structured, retryable HTTP error — 429 (quota, after key rotation) or
# 503 (busy) — instead of fabricating a verdict or freezing the client.
# ======================================================================
_BUSY_DETAIL = {
    "code": "vision_busy",
    "retry": "busy",
    "message": (
        "The vision model (Gemini 3.8 Flash) did not respond within its full "
        "45-second budget. Nothing was saved. Please retry in a moment — "
        "congestion is usually temporary."
    ),
}

#: Daily free-tier allowance for the vision model ran out — Google answers
#: 429 RESOURCE_EXHAUSTED with its own reset window. Mirrored to the
#: client as HTTP 429 with code ``vision_quota``: instant, structured,
#: zero network calls once the key pool knows every key is spent. Served
#: only after ALL keys were rotated or were already memoised-blocked.
_QUOTA_DETAIL = {
    "code": "vision_quota",
    "retry": "quota",
    "message": (
        "The free daily allowance for Gemini 3.8 Flash is exhausted "
        "(20 requests/day per key/project) — every configured key was tried "
        "automatically. Nothing was saved. The allowance resets later today "
        "per Google's schedule; you can also add a backup key "
        "(GEMINI_API_KEY_1) to .env for additional daily allowance."
    ),
}


def _busy(exc: AgentFailure) -> HTTPException:
    logger.warning("Agent failure (kind=%s, agent=%s): %s", exc.kind, exc.agent, exc)
    if exc.kind == "quota":
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_QUOTA_DETAIL
        )
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_BUSY_DETAIL)


# ======================================================================
# Helpers
# ======================================================================
async def _owned_plot_id(db: AsyncSession, user_id: str, plot_id: str | None) -> str | None:
    """Validate that ``plot_id`` belongs to the caller; return it."""
    if not plot_id:
        return None
    result = await db.execute(
        select(Plot.id)
        .join(Farm, Plot.farm_id == Farm.id)
        .where(Plot.id == plot_id, Farm.owner_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plot not found")
    return plot_id


async def _owned_diagnosis(
    db: AsyncSession, user_id: str, diagnosis_id: str, *, for_update: bool = False
) -> Diagnosis:
    diagnosis = await db.get(Diagnosis, diagnosis_id)
    if diagnosis is None or diagnosis.is_deleted or diagnosis.farmer_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis not found")
    return diagnosis


async def _store_image_if_enabled(validated: ValidatedImage) -> str | None:
    """Persist the analysed photo only when ``STORE_IMAGES=true`` (privacy)."""
    settings = get_settings()
    if not settings.STORE_IMAGES:
        return None
    try:
        from PIL import Image  # noqa: F401 - import guard for exotic builds

        directory = Path(getattr(settings, "PROJECT_DIR", ".")) / settings.IMAGE_STORAGE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid4().hex}.jpg"

        def _save() -> None:
            validated.image.convert("RGB").save(path, format="JPEG", quality=90)

        await asyncio.to_thread(_save)
        return str(path)
    except Exception:  # noqa: BLE001 - storage must never break a diagnosis
        logger.warning("Could not store image", exc_info=True)
        return None


def _persist_common(
    diagnosis: Diagnosis,
    *,
    user_id: str,
    plot_id: str | None,
    client_id: str | None,
    notes: str | None,
    crop: dict,
) -> Diagnosis:
    """Apply the fields shared by every pipeline branch (from the report)."""
    diagnosis.farmer_id = user_id
    if plot_id is not None:
        diagnosis.plot_id = plot_id
    if client_id:
        diagnosis.client_id = client_id
    diagnosis.crop_id = crop.get("id")
    diagnosis.crop_label = crop.get("label")
    diagnosis.crop_confidence = crop.get("confidence")
    diagnosis.crop_source = crop.get("source")
    if notes is not None:
        diagnosis.notes = notes[:4000]
    diagnosis.client_updated_at = utcnow()
    return diagnosis


# ======================================================================
# Endpoints
# ======================================================================
@router.post("/diagnose", response_model=DiagnosisResponse)
async def diagnose(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="Photo of the affected leaf/plant (JPG/PNG/WEBP)"),
    selected_crop: str | None = Form(
        None, description="Crop id/label chosen from Stage-1 candidates (skips Stage 1)"
    ),
    plot_id: str | None = Form(None, description="Plot the photo belongs to"),
    diagnosis_id: str | None = Form(
        None, description="Continue an existing diagnosis (returned by a previous call)"
    ),
    client_id: str | None = Form(
        None, description="Offline idempotency key — re-using it updates instead of duplicating"
    ),
    notes: str | None = Form(None, description="Free-form notes from the farmer"),
) -> DiagnosisResponse:
    """Run the multi-agent crop diagnosis pipeline on an uploaded photo."""
    settings = get_settings()

    # --- 0. Read a strictly bounded amount of bytes --------------------
    raw = await file.read(settings.IMAGE_MAX_BYTES + 1)
    if len(raw) > settings.IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "image_too_large",
                "message": (
                    f"Image exceeds the {settings.IMAGE_MAX_BYTES // (1024 * 1024)} MB limit."
                ),
            },
        )

    # --- 1. Quality gate (local blur/size checks) ----------------------
    # Fully local — wrap them so an exotic decode can never surface as a
    # 500 (structured 422 retake guidance instead).
    try:
        validated: ValidatedImage = await asyncio.to_thread(validate_crop_image, raw)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never crash on quality checks
        logger.exception("Image quality check failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_unreadable",
                "message": "The photo could not be analysed. Please retake it.",
            },
        ) from exc

    # --- 2. Ownership checks -------------------------------------------
    await _owned_plot_id(db, user.id, plot_id)

    diagnosis: Diagnosis | None = None
    if diagnosis_id:
        diagnosis = await _owned_diagnosis(db, user.id, diagnosis_id)
    elif client_id:
        result = await db.execute(
            select(Diagnosis).where(
                Diagnosis.farmer_id == user.id, Diagnosis.client_id == client_id
            )
        )
        diagnosis = result.scalar_one_or_none()

    # --- 3. Blur gate: prompt for a retake, never call the agents ------
    if validated.is_blurry:
        diagnosis = diagnosis or Diagnosis(id=new_id())
        _persist_common(
            diagnosis,
            user_id=user.id,
            plot_id=plot_id,
            client_id=client_id,
            notes=notes,
            crop={"id": None, "label": None, "confidence": None, "source": None},
        )
        diagnosis.status = DiagnosisStatus.RETAKE_REQUIRED.value
        diagnosis.stage = 1
        diagnosis.disease_id = None
        diagnosis.disease_label = None
        diagnosis.disease_confidence = None
        diagnosis.treatment_plan = None
        diagnosis.crop_candidates = None
        diagnosis.image_path = await _store_image_if_enabled(validated)
        diagnosis.model_meta = {"quality": validated.as_report(), "source": "quality_gate"}
        db.add(diagnosis)
        await db.commit()
        await db.refresh(diagnosis)

        message = (
            "The photo is too blurry (sharpness "
            f"{validated.blur_variance:.1f} < {settings.IMAGE_BLUR_MIN_VARIANCE:.1f}). "
            "Please retake it: hold the phone steady, focus on a single affected leaf and "
            "use bright, indirect daylight."
        )
        return DiagnosisResponse.from_diagnosis(
            diagnosis,
            message=message,
            image_quality=validated.as_report(),  # type: ignore[arg-type]
        )

    # --- 4. Run the multi-agent pipeline (supervisor) ------------------
    previous: list[CropCandidate] = []
    if diagnosis is not None and diagnosis.crop_candidates:
        previous = [CropCandidate.model_validate(c) for c in diagnosis.crop_candidates]

    ctx = AgentContext(
        image=validated.image,
        raw_bytes=raw,
        raw_mime=raw_mime_for(validated.format),
        byte_size=validated.original_bytes,
        selected_crop=selected_crop,
        notes=notes,
        farm_location=None,
        lang="en",  # English-first standard (UI strings are client-side)
    )
    try:
        report = await SupervisorAgent().analyze(
            ctx, previous_candidates=previous, quality_report=validated.as_report()
        )
    except AgentFailure as exc:
        raise _busy(exc) from exc

    # --- 5. Persist the unified report ---------------------------------
    crop = report["crop"]
    disease = report["disease"]
    candidates = [CropCandidate.model_validate(c) for c in report["candidates"]]

    diagnosis = diagnosis or Diagnosis(id=new_id())
    _persist_common(
        diagnosis,
        user_id=user.id,
        plot_id=plot_id,
        client_id=client_id,
        notes=notes,
        crop=crop,
    )
    diagnosis.status = report["status"]
    diagnosis.stage = report["stage"]
    if disease:
        diagnosis.disease_id = slugify(str(disease.get("name_en") or ""))
        diagnosis.disease_label = str(disease.get("name_en") or "")[:160]
        diagnosis.disease_confidence = disease.get("confidence")
    else:
        diagnosis.disease_id = None
        diagnosis.disease_label = None
        diagnosis.disease_confidence = None
    diagnosis.treatment_plan = report["treatment_plan"]
    diagnosis.crop_candidates = (
        [c.model_dump() for c in candidates] if candidates else diagnosis.crop_candidates
    )
    diagnosis.image_path = await _store_image_if_enabled(validated)
    diagnosis.model_meta = report["model_meta"]
    db.add(diagnosis)
    await db.commit()
    await db.refresh(diagnosis)

    logger.info(
        "Diagnosis %s [%s]: crop=%s (%.2f, %s) status=%s",
        diagnosis.id,
        report["pipeline"],
        crop.get("id"),
        crop.get("confidence") or 0.0,
        crop.get("source"),
        report["status"],
    )

    return DiagnosisResponse.from_diagnosis(
        diagnosis,
        candidates=candidates,
        message=report["message"],
        image_quality=validated.as_report(),  # type: ignore[arg-type]
        treatment_plan=(
            TreatmentPlan.model_validate(report["treatment_plan"])
            if report["treatment_plan"]
            else None
        ),
    )


@router.get("/diagnoses", response_model=DiagnosisListResponse)
async def list_diagnoses(
    db: DbSession,
    user: CurrentUser,
    status_filter: str | None = Query(None, alias="status", description="Filter by diagnosis status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DiagnosisListResponse:
    """List the caller's diagnoses (newest first)."""
    query = select(Diagnosis).where(
        Diagnosis.farmer_id == user.id, Diagnosis.is_deleted.is_(False)
    )
    if status_filter:
        if status_filter not in _STATUS_VALUES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown status '{status_filter}'. Valid values: {sorted(_STATUS_VALUES)}",
            )
        query = query.where(Diagnosis.status == status_filter)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(Diagnosis.updated_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return DiagnosisListResponse(
        items=[DiagnosisOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/diagnoses/{diagnosis_id}", response_model=DiagnosisOut)
async def get_diagnosis(diagnosis_id: str, db: DbSession, user: CurrentUser) -> DiagnosisOut:
    """Fetch a single diagnosis (owner only)."""
    diagnosis = await _owned_diagnosis(db, user.id, diagnosis_id)
    return DiagnosisOut.model_validate(diagnosis)


@router.delete("/diagnoses/{diagnosis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_diagnosis(diagnosis_id: str, db: DbSession, user: CurrentUser) -> None:
    """Soft-delete a diagnosis (privacy: the farmer can erase their data)."""
    diagnosis = await _owned_diagnosis(db, user.id, diagnosis_id)
    diagnosis.is_deleted = True
    diagnosis.client_updated_at = utcnow()
    db.add(diagnosis)
    await db.commit()


# The key pool is exposed for /health-style observability (count only).
__all__ = ["router", "KeyPoolManager"]
