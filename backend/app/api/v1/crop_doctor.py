"""Crop Doctor — the two-stage diagnostic pipeline (Gemini vision).

Stage 1 — **Crop identification** (``disease_detector.analyze_full``)
    * Validates the upload (size, decodability, dimensions, blur/Laplacian).
    * ONE Gemini multimodal shot with a generous deadline (default 45s —
      the free tier commonly needs 20-40s, so healthy answers are never
      cut off prematurely; UI shows a staged loading state meanwhile).
    * confidence >= ``CROP_AUTO_THRESHOLD`` (0.80) → routed to Stage 2.
    * confidence <  0.80 → top-3 candidates are returned for the farmer to pick.

Stage 2 — **Deep disease analysis** (``disease_detector.analyze_disease``)
    * ONE Gemini multimodal shot (same budget) → Arabic disease name,
      confidence, symptoms, immediate/cultural/chemical treatments.
    * confidence >= ``DISEASE_CONFIRM_THRESHOLD`` (0.75) → confirmed diagnosis
      + Arabic-first treatment plan (knowledge-base enriched).
    * confidence in [0.50, 0.75) → agronomist review queue (``in_review``).
    * confidence <  0.50 → farmer is asked to retake the photo.

**Live-only contract** — there is no mock/simulated verdict. When Gemini
cannot answer the stage raises ``VisionUnavailable`` and this endpoint
answers with a structured, *user-friendly* error instead of a fabricated
diagnosis: **429** (``code: vision_quota``) when the daily free-tier
allowance is spent (after automatic failover through every configured
key), **503** (``code: vision_busy``) for congestion/timeouts — both
carry a clear Arabic message and never freeze the UI.

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

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.database import new_id, utcnow
from app.models.diagnosis import CropSource, Diagnosis, DiagnosisStatus
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
from app.services.disease_detector import (
    VisionUnavailable,
    analyze_disease,
    analyze_full,
    candidate_objects,
    raw_mime_for,
    treatment_plan_from_analysis,
)

logger = logging.getLogger("hassad.api.crop_doctor")

router = APIRouter(prefix="/crop-doctor", tags=["Crop Doctor"])

_STATUS_VALUES = {status_.value for status_ in DiagnosisStatus}

# ======================================================================
# Live-only honesty: when Gemini cannot answer inside its bounded budget
# the pipeline raises VisionUnavailable (no mock exists) and we return a
# structured, retryable HTTP error — 429 (quota, after key failover) or
# 503 (busy) — instead of fabricating a verdict or freezing the client.
# ======================================================================
_BUSY_DETAIL = {
    "code": "vision_busy",
    "retry": "busy",
    "message": (
        "النموذج الزراعي (Gemini 3.8 Flash) لم يستجب رغم المهلة الكافية (حتى 45 ثانية) "
        "لتحليل الصورة. لم تُحفظ أي نتيجة. أعد المحاولة خلال لحظات — الازدحام مؤقت عادة."
    ),
}

#: Daily free-tier allowance for the vision model ran out — Google answers
#: 429 RESOURCE_EXHAUSTED with its own reset window (seconds-based retry
#: advice would be wrong here). Mirrored to the client as HTTP 429 with
#: code ``vision_quota``: instant, structured, zero network calls once the
#: in-process memo knows every key in the chain is spent. The message
#: mentions the automatic key failover because this detail is only served
#: after ALL configured keys were tried or were already memoised-blocked.
_QUOTA_DETAIL = {
    "code": "vision_quota",
    "retry": "quota",
    "message": (
        "استُنفدت الحصّة اليومية المجانية لنموذج Gemini 3.8 Flash (20 طلباً في اليوم "
        "لكل مفتاح/مشروع) — تمت محاولة كل المفاتيح المهيّأة تلقائياً. لم تُحفظ أي نتيجة. "
        "ستتجدد الحصّة لاحقاً بحسب وقت Google؛ يمكنك أيضاً إضافة مفتاح احتياطي "
        "(GEMINI_API_KEY_1) في ملف الإعداد لاستقبال الحصّات المتعددة."
    ),
}


def _busy(exc: VisionUnavailable) -> HTTPException:
    logger.warning("Vision unavailable (kind=%s): %s", exc.kind, exc)
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


def _resolve_crop_selection(selected: str, previous: list[CropCandidate]) -> tuple[str, str]:
    """Map the farmer's pick (id or label) back to the Stage-1 candidate."""
    needle = selected.strip().lower()
    for candidate in previous:
        if candidate.id.lower() == needle or candidate.label.lower() == needle:
            return candidate.id, candidate.label
    return slugify(selected), selected.strip()


async def _store_image_if_enabled(validated: ValidatedImage) -> str | None:
    """Persist the analysed photo only when ``STORE_IMAGES=true`` (privacy)."""
    settings = get_settings()
    if not settings.STORE_IMAGES:
        return None
    try:
        from PIL import Image

        directory = Path(settings.PROJECT_DIR if hasattr(settings, "PROJECT_DIR") else ".") / settings.IMAGE_STORAGE_DIR
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
    crop_id: str | None,
    crop_label: str | None,
    crop_confidence: float | None,
    crop_source: str | None,
    notes: str | None,
) -> Diagnosis:
    """Apply the fields shared by every pipeline branch."""
    diagnosis.farmer_id = user_id
    if plot_id is not None:
        diagnosis.plot_id = plot_id
    if client_id:
        diagnosis.client_id = client_id
    diagnosis.crop_id = crop_id
    diagnosis.crop_label = crop_label
    diagnosis.crop_confidence = crop_confidence
    diagnosis.crop_source = crop_source
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
    """Run the two-stage crop disease pipeline on an uploaded photo."""
    settings = get_settings()
    started = time.perf_counter()

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

    # --- 1. Stage-1 validation (blur / size / decodable) ---------------
    # Quality checks are fully local — wrap them so an exotic decode can
    # never surface as a 500 (structured 422 retake guidance instead).
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

    # --- 3. Blur gate: prompt for a retake, never call the models ------
    if validated.is_blurry:
        diagnosis = diagnosis or Diagnosis(id=new_id())
        _persist_common(
            diagnosis,
            user_id=user.id,
            plot_id=plot_id,
            client_id=client_id,
            crop_id=None,
            crop_label=None,
            crop_confidence=None,
            crop_source=None,
            notes=notes,
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

    # --- 4. Stage 1: crop classification ------------------------------
    candidates: list[CropCandidate] = []
    crop_id: str | None = None
    crop_label: str | None = None
    crop_confidence: float | None = None
    crop_source: str | None = None
    crop_engine = "user"  # "user" | "gemini"
    disease_analysis: dict | None = None  # verdict, filled by the one-shot path
    raw_mime = raw_mime_for(validated.format)

    if selected_crop and selected_crop.strip():
        previous: list[CropCandidate] = []
        if diagnosis is not None and diagnosis.crop_candidates:
            previous = [CropCandidate.model_validate(c) for c in diagnosis.crop_candidates]
        crop_id, crop_label = _resolve_crop_selection(selected_crop, previous)
        crop_confidence = 1.0
        crop_source = CropSource.USER_SELECTED.value
    else:
        # ---- Stages 1+2 in ONE live Gemini shot (bounded) ---------------
        # Uploaded bytes are forwarded directly (see _image_part); failure
        # raises VisionUnavailable → structured 503, never a fake verdict.
        try:
            full_analysis = await analyze_full(
                validated.image,
                byte_size=validated.original_bytes,
                raw_bytes=raw,
                raw_mime=raw_mime,
            )
        except VisionUnavailable as exc:
            raise _busy(exc) from exc
        crop_engine = str(full_analysis["engine"])  # always "gemini"
        candidates = candidate_objects(full_analysis["candidates"])
        if not candidates:  # defensive: never 500 — ask the farmer instead
            candidates = [CropCandidate(id="tomato", label="طماطم", confidence=0.5)]
        top_crop = candidates[0]
        crop_id, crop_label, crop_confidence = top_crop.id, top_crop.label, top_crop.confidence

        if top_crop.confidence < settings.CROP_AUTO_THRESHOLD:
            # ---- Stage 1 stops here: ask the farmer to pick a crop ----
            crop_source = CropSource.MODEL_CANDIDATES.value
            diagnosis = diagnosis or Diagnosis(id=new_id())
            _persist_common(
                diagnosis,
                user_id=user.id,
                plot_id=plot_id,
                client_id=client_id,
                crop_id=crop_id,
                crop_label=crop_label,
                crop_confidence=crop_confidence,
                crop_source=crop_source,
                notes=notes,
            )
            diagnosis.status = DiagnosisStatus.AWAITING_CROP_SELECTION.value
            diagnosis.stage = 1
            diagnosis.disease_id = None
            diagnosis.disease_label = None
            diagnosis.disease_confidence = None
            diagnosis.treatment_plan = None
            diagnosis.crop_candidates = [c.model_dump() for c in candidates]
            diagnosis.image_path = await _store_image_if_enabled(validated)
            diagnosis.model_meta = {
                "quality": validated.as_report(),
                "source": "model",
                "vision": {
                    "crop": crop_engine,
                    "model": settings.vision_model_name,
                    "latency_ms": full_analysis["latency_ms"],
                },
            }
            db.add(diagnosis)
            await db.commit()
            await db.refresh(diagnosis)

            labels = "، ".join(f"{c.label} ({c.confidence:.0%})" for c in candidates)
            return DiagnosisResponse.from_diagnosis(
                diagnosis,
                candidates=candidates,
                requires_crop_selection=True,
                message=(
                    f"لم أتمكن من تحديد المحصول بثقة كافية (أفضل تخمين: {top_crop.label} "
                    f"بثقة {top_crop.confidence:.0%}). الرجاء اختيار أحد المرشحات وإعادة "
                    f"إرسال الصورة مع selected_crop: {labels}"
                ),
                image_quality=validated.as_report(),  # type: ignore[arg-type]
            )

        crop_source = CropSource.MODEL_AUTO.value
        # The same single response already carries the deep disease verdict.
        disease_analysis = full_analysis

    # --- 5. Stage 2: deep disease verdict -------------------------------
    if disease_analysis is None:
        # Farmer confirmed the crop: ONE fresh live Gemini shot. Failure
        # → VisionUnavailable → structured 503 (no fabricated verdict).
        try:
            disease_analysis = await analyze_disease(
                validated.image,
                crop_label=crop_label or "",
                crop_id=crop_id,
                byte_size=validated.original_bytes,
                raw_bytes=raw,
                raw_mime=raw_mime,
            )
        except VisionUnavailable as exc:
            raise _busy(exc) from exc
    disease_engine = str(disease_analysis["engine"])  # always "gemini"
    disease_confidence = round(min(max(float(disease_analysis["confidence"]), 0.0), 1.0), 4)
    disease_id = slugify(disease_analysis["name_en"])
    disease_label = disease_analysis["name_ar"]

    treatment_plan: dict[str, object] | None = None
    if disease_confidence >= settings.DISEASE_CONFIRM_THRESHOLD:
        new_status = DiagnosisStatus.CONFIRMED
        treatment_plan = treatment_plan_from_analysis(disease_analysis, crop_label).model_dump()
        message = (
            f"تم التأكيد: {disease_label} على {crop_label} "
            f"(ثقة {disease_confidence:.0%}). اتبع خطة المعالجة أدناه."
        )
    elif disease_confidence >= settings.DISEASE_REVIEW_THRESHOLD:
        new_status = DiagnosisStatus.IN_REVIEW
        message = (
            f"احتمال {disease_label} على {crop_label} (ثقة {disease_confidence:.0%}). "
            "تم إرسال الحالة إلى طابور مراجعة مهندسي الزراعة — ستحصل على خطة معالجة "
            "مؤكدة قريبًا."
        )
    else:
        new_status = DiagnosisStatus.RETAKE_REQUIRED
        message = (
            f"ثقة منخفضة ({disease_confidence:.0%}) — لم أتمكّن من تحديد المرض. "
            "أعد التصوير: اقترب من ورقة مصابة واحدة، أبقِ الورقة مسطّحة، واستخدم إضاءة "
            "نهارية غير مباشرة وخلفية واضحة."
        )

    # --- 6. Persist stage-2 results ------------------------------------
    diagnosis = diagnosis or Diagnosis(id=new_id())
    _persist_common(
        diagnosis,
        user_id=user.id,
        plot_id=plot_id,
        client_id=client_id,
        crop_id=crop_id,
        crop_label=crop_label,
        crop_confidence=crop_confidence,
        crop_source=crop_source,
        notes=notes,
    )
    diagnosis.stage = 2
    diagnosis.status = new_status.value
    diagnosis.disease_id = disease_id
    diagnosis.disease_label = disease_label
    diagnosis.disease_confidence = disease_confidence
    diagnosis.treatment_plan = treatment_plan
    diagnosis.crop_candidates = (
        [c.model_dump() for c in candidates] if candidates else diagnosis.crop_candidates
    )
    diagnosis.image_path = await _store_image_if_enabled(validated)
    diagnosis.model_meta = {
        "source": "model",
        "vision": {
            "crop": crop_engine,
            "disease": disease_engine,
            "model": settings.vision_model_name,
            "latency_ms": disease_analysis["latency_ms"],
        },
        "alternatives": disease_analysis.get("alternatives", []),
        "quality": validated.as_report(),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "thresholds": {
            "crop_auto": settings.CROP_AUTO_THRESHOLD,
            "disease_confirm": settings.DISEASE_CONFIRM_THRESHOLD,
            "disease_review": settings.DISEASE_REVIEW_THRESHOLD,
        },
    }
    db.add(diagnosis)
    await db.commit()
    await db.refresh(diagnosis)

    logger.info(
        "Diagnosis %s: crop=%s (%.2f, %s) disease=%s (%.2f) status=%s",
        diagnosis.id,
        crop_id,
        crop_confidence or 0.0,
        crop_source,
        disease_id,
        disease_confidence,
        new_status.value,
    )

    return DiagnosisResponse.from_diagnosis(
        diagnosis,
        candidates=candidates,
        message=message,
        image_quality=validated.as_report(),  # type: ignore[arg-type]
        treatment_plan=TreatmentPlan.model_validate(treatment_plan) if treatment_plan else None,
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
