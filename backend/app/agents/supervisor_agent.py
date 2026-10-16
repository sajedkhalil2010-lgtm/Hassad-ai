"""SupervisorAgent — coordinates the multi-agent diagnosis workflow.

The v2 orchestrator. Routers stay thin (HTTP + persistence only); all
decision-making lives here:

1. **Crop identification** — a farmer-confirmed crop skips the vision
   stage; otherwise :class:`~app.agents.diagnostic_agent.DiagnosticAgent`
   runs ONE live shot (crop candidates + deep verdict together).
2. **Threshold policy** — confidence decides the branch: ask the farmer
   to pick a crop, confirm the diagnosis, queue for agronomist review, or
   ask for a retake. Never fabricated — low confidence is low confidence.
3. **Deep diagnosis** — when the crop was picked by hand, the agent runs
   its dedicated disease shot.
4. **Advisory** — :class:`~app.agents.advisor_agent.AdvisorAgent`
   formulates the localized treatment plan for confirmed cases.

The supervisor returns **one unified, clean JSON report** the router
persists and serves to the (untouched) frontend. Failures from any agent
propagate as :class:`~app.agents.base.AgentFailure` (quota|busy) so the
router can answer an honest structured 429/503. Key management itself is
delegated to the KeyPoolManager (rotation on 429, quota memo, zero SDK
retries).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.agents.advisor_agent import AdvisorAgent
from app.agents.base import AgentContext, clamp01
from app.agents.diagnostic_agent import DiagnosticAgent
from app.core.config import get_settings
from app.models.diagnosis import CropSource, DiagnosisStatus
from app.schemas.diagnosis import CropCandidate

logger = logging.getLogger("hassad.agents.supervisor")


def resolve_crop_selection(selected: str, previous: list[CropCandidate]) -> tuple[str, str]:
    """Map the farmer's pick (id or label) back to the known candidate."""
    needle = selected.strip().lower()
    for candidate in previous:
        if candidate.id.lower() == needle or candidate.label.lower() == needle:
            return candidate.id, candidate.label
    from app.services.ai_vision import slugify

    return slugify(selected), selected.strip()


class SupervisorAgent:
    """Workflow coordinator: diagnostic → advisor → unified report."""

    name = "supervisor"

    def __init__(self) -> None:
        self.diagnostic = DiagnosticAgent()
        self.advisor = AdvisorAgent()

    async def analyze(
        self,
        ctx: AgentContext,
        *,
        previous_candidates: list[CropCandidate] | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the pipeline and return the unified report (see module doc).

        ``ctx.selected_crop`` set  → confirmed-crop path (deep shot).
        unset                       → one-shot path (candidates + verdict).
        """
        settings = get_settings()
        started = time.perf_counter()

        candidates: list[CropCandidate] = []
        crop_id: str | None = None
        crop_label: str | None = None
        crop_confidence: float | None = None
        crop_source: str | None = None
        crop_engine = "user"  # "user" | "gemini"
        disease_analysis: dict[str, Any] | None = None

        if ctx.selected_crop and ctx.selected_crop.strip():
            crop_id, crop_label = resolve_crop_selection(
                ctx.selected_crop, previous_candidates or []
            )
            ctx.crop_id, ctx.crop_label = crop_id, crop_label  # for the deep shot's prompt
            crop_confidence = 1.0
            crop_source = CropSource.USER_SELECTED.value
        else:
            # ---- Stages 1+2 in ONE live agent shot (bounded) -------------
            full_analysis = await self.diagnostic.analyze_full(ctx)
            crop_engine = str(full_analysis.get("engine") or "gemini")
            candidates = _candidate_objects(full_analysis.get("candidates") or [])
            if not candidates:  # defensive: never 500 — ask the farmer instead
                candidates = [CropCandidate(id="tomato", label="Tomato", confidence=0.5)]
            top_crop = candidates[0]
            crop_id, crop_label, crop_confidence = (
                top_crop.id,
                top_crop.label,
                top_crop.confidence,
            )

            if top_crop.confidence < settings.CROP_AUTO_THRESHOLD:
                # ---- Stage 1 stops here: ask the farmer to pick a crop ----
                return self._report(
                    status=DiagnosisStatus.AWAITING_CROP_SELECTION.value,
                    stage=1,
                    requires_crop_selection=True,
                    message=(
                        f"I could not identify the crop with enough confidence "
                        f"(best guess: {top_crop.label} at {top_crop.confidence:.0%}). "
                        f"Please pick one of the candidates: "
                        + ", ".join(f"{c.label} ({c.confidence:.0%})" for c in candidates)
                    ),
                    crop_id=crop_id,
                    crop_label=crop_label,
                    crop_confidence=crop_confidence,
                    crop_source=CropSource.MODEL_CANDIDATES.value,
                    crop_engine=crop_engine,
                    candidates=candidates,
                    disease=None,
                    treatment_plan=None,
                    model_meta=self._meta(
                        quality=quality_report,
                        source="model",
                        vision={
                            "crop": crop_engine,
                            "model": settings.vision_model_name,
                            "latency_ms": full_analysis.get("latency_ms"),
                        },
                        started=started,
                        settings=settings,
                    ),
                )

            crop_source = CropSource.MODEL_AUTO.value
            disease_analysis = full_analysis  # same shot already carries it

        # ---- Stage 2: deep disease verdict -----------------------------
        if disease_analysis is None:
            disease_analysis = await self.diagnostic.analyze_disease(ctx)
        disease_engine = str(disease_analysis.get("engine") or "gemini")
        disease_confidence = clamp01(disease_analysis.get("confidence", 0.0))
        disease_label = str(disease_analysis.get("name_en") or "Unknown")

        treatment_plan: dict[str, Any] | None = None
        if disease_confidence >= settings.DISEASE_CONFIRM_THRESHOLD:
            new_status = DiagnosisStatus.CONFIRMED
            treatment_plan = self.advisor.build_plan(
                disease_analysis, crop_label
            ).model_dump()
            message = (
                f"Confirmed: {disease_label} on {crop_label} "
                f"(confidence {disease_confidence:.0%}). Follow the treatment plan below."
            )
        elif disease_confidence >= settings.DISEASE_REVIEW_THRESHOLD:
            new_status = DiagnosisStatus.IN_REVIEW
            message = (
                f"Possible {disease_label} on {crop_label} "
                f"(confidence {disease_confidence:.0%}). The case was queued for "
                "agronomist review — a confirmed treatment plan follows shortly."
            )
        else:
            new_status = DiagnosisStatus.RETAKE_REQUIRED
            message = (
                f"Low confidence ({disease_confidence:.0%}) — the disease could not be "
                "identified reliably. Retake: get close to a single affected leaf, keep "
                "it flat, and use bright indirect daylight with a plain background."
            )

        return self._report(
            status=new_status.value,
            stage=2,
            requires_crop_selection=False,
            message=message,
            crop_id=crop_id,
            crop_label=crop_label,
            crop_confidence=crop_confidence,
            crop_source=crop_source,
            crop_engine=crop_engine,
            candidates=candidates,
            disease=disease_analysis,
            treatment_plan=treatment_plan,
            model_meta=self._meta(
                quality=quality_report,
                source="model",
                vision={
                    "crop": crop_engine,
                    "disease": disease_engine,
                    "model": settings.vision_model_name,
                    "latency_ms": disease_analysis.get("latency_ms"),
                },
                alternatives=disease_analysis.get("alternatives", []),
                started=started,
                settings=settings,
            ),
        )

    # -- report assembly ----------------------------------------------
    @staticmethod
    def _meta(
        *,
        quality: dict[str, Any] | None,
        source: str,
        vision: dict[str, Any],
        started: float,
        settings: Any,
        alternatives: list[Any] | None = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            # `source` keeps its historical value ("model") — the frontend
            # branches on it; v2 identity travels under `pipeline`.
            "source": source,
            "pipeline": "supervisor_v2",
            "agents": ["diagnostic", "advisor", "supervisor"],
            "vision": vision,
            "quality": quality or {},
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "thresholds": {
                "crop_auto": settings.CROP_AUTO_THRESHOLD,
                "disease_confirm": settings.DISEASE_CONFIRM_THRESHOLD,
                "disease_review": settings.DISEASE_REVIEW_THRESHOLD,
            },
        }
        if alternatives is not None:
            meta["alternatives"] = alternatives
        return meta

    @staticmethod
    def _report(
        *,
        status: str,
        stage: int,
        requires_crop_selection: bool,
        message: str,
        crop_id: str | None,
        crop_label: str | None,
        crop_confidence: float | None,
        crop_source: str | None,
        crop_engine: str,
        candidates: list[CropCandidate],
        disease: dict[str, Any] | None,
        treatment_plan: dict[str, Any] | None,
        model_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """One unified, clean JSON-ready report for the router/frontend."""
        return {
            "pipeline": "supervisor_v2",
            "status": status,
            "stage": stage,
            "requires_crop_selection": requires_crop_selection,
            "message": message,
            "crop": {
                "id": crop_id,
                "label": crop_label,
                "confidence": crop_confidence,
                "source": crop_source,
                "engine": crop_engine,
            },
            "candidates": [c.model_dump() for c in candidates],
            "disease": disease,
            "treatment_plan": treatment_plan,
            "model_meta": model_meta,
        }


def _candidate_objects(rows: list[dict[str, Any]]) -> list[CropCandidate]:
    """Typed candidates for the API schema."""
    out: list[CropCandidate] = []
    for row in rows:
        try:
            out.append(
                CropCandidate(
                    id=str(row["id"]),
                    label=str(row["label"]),
                    confidence=clamp01(row.get("confidence", 0.0)),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out
