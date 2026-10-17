"""Analyze — direct quantitative pathology report (developer/presentation API).

``POST /api/v1/analyze`` accepts a multipart leaf photo (plus an optional
``crop`` id) and returns the SupervisorAgent's full structured report:
measured metrics (affected area %, Dice consensus, severity, lesion
geometry, colour fractions), the matched condition, the Tébessa
localized advisory (treatment + irrigation + seasonal calendar) and the
agent execution trace — in one JSON body.

Unlike ``/crop-doctor/diagnose`` this endpoint is **stateless**: nothing
is persisted (no diagnosis row, no image), so it is safe to call from
scripts, slides and field demos. It requires an authenticated user like
every other API route. The untouched frontend uses the Crop Doctor
router; this route exists for direct API consumers.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.agents import AgentContext, AgentFailure, SupervisorAgent
from app.agents.base import raw_mime_for
from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.services.ai_vision import ValidatedImage, validate_crop_image

logger = logging.getLogger("hassad.api.analyze")

router = APIRouter(prefix="/analyze", tags=["Analyze"])


@router.post("", summary="Quantitative leaf analysis (stateless)")
async def analyze(
    user: CurrentUser,  # authenticated like every route; nothing is persisted
    db: DbSession,  # kept for signature parity with the other routers
    file: UploadFile = File(..., description="Photo of the affected leaf (JPG/PNG/WEBP)"),
    crop: str | None = Form(None, description="Optional crop id (skips the selection stage)"),
    notes: str | None = Form(None, description="Free-form notes (not persisted)"),
) -> dict:
    """Run the local multi-agent pipeline and return the full JSON report."""
    settings = get_settings()

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

    if validated.is_blurry:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_blurry",
                "message": (
                    f"The photo is too blurry (sharpness {validated.blur_variance:.1f} < "
                    f"{settings.IMAGE_BLUR_MIN_VARIANCE:.1f}). Retake with steady hands "
                    "and bright, indirect daylight."
                ),
            },
        )

    ctx = AgentContext(
        image=validated.image,
        raw_bytes=raw,
        raw_mime=raw_mime_for(validated.format),
        byte_size=validated.original_bytes,
        selected_crop=crop,
        notes=notes,
        lang="en",
    )

    supervisor = SupervisorAgent()
    try:
        report = await supervisor.analyze(ctx, quality_report=validated.as_report())
    except AgentFailure as exc:
        logger.warning("Analyze agent failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "vision_busy",
                "retry": "busy",
                "message": "The local analysis engine could not finish. Please retry.",
            },
        ) from exc

    # Enrich with the Tébessa regional advisory block (irrigation,
    # climate, soil, seasonal calendar) for API consumers.
    disease = report.get("disease") or {}
    extras = supervisor.advisor.advisory_extras(
        disease, (report.get("crop") or {}).get("label")
    )
    candidates = [c.model_dump() for c in report["candidates"]]

    return {
        "pipeline": report["pipeline"],
        "status": report["status"],
        "stage": report["stage"],
        "requires_crop_selection": report["requires_crop_selection"],
        "message": report["message"],
        "quantitative": report.get("quantitative"),
        "crop": report["crop"],
        "candidates": candidates,
        "diagnosis": (
            {
                "id": disease.get("condition_id") or _slug(disease.get("name_en") or ""),
                "name_en": disease.get("name_en"),
                "name_ar": disease.get("name_ar"),
                "confidence": disease.get("confidence"),
                "summary": disease.get("summary"),
                "symptoms": disease.get("symptoms") or [],
                "alternatives": disease.get("alternatives") or [],
            }
            if disease
            else None
        ),
        "advisory": {
            "treatment_plan": report.get("treatment_plan"),
            "regional": extras,
        },
        "image_quality": report.get("model_meta", {}).get("quality") or {},
        "engine": report.get("model_meta", {}).get("vision") or {},
        "trace": report.get("model_meta", {}).get("trace") or [],
    }


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "condition"
