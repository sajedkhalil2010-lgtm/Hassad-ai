"""SupervisorAgent — autonomous coordination of the diagnosis workflow.

v3: the supervisor no longer walks a straight line. It is a genuine
**coordination loop**:

1. **Plan** — from the run inputs it builds a stage plan (a
   farmer-confirmed crop skips vision identification entirely; the
   auto path runs one shot and only continues when the threshold policy
   allows).
2. **Dispatch → evaluate → decide** — each stage is dispatched to the
   right agent, its result is evaluated against the decision policy, and
   the supervisor decides the next dispatch: continue, ask the farmer to
   pick a crop, ask for a retake, queue for agronomist review, or
   proceed to the advisory stage. Every decision is recorded on the
   shared blackboard (``ctx.trace``) and shipped in the report.
3. **Report** — one unified, clean JSON report the router persists and
   serves to the (untouched) frontend.

Stage policy (settings-driven thresholds):

* crop confidence < ``CROP_AUTO_THRESHOLD`` → ask the farmer (top-3
  candidates shipped back).
* disease confidence ≥ ``DISEASE_CONFIRM_THRESHOLD`` → confirmed +
  AdvisorAgent localized treatment plan.
* ≥ ``DISEASE_REVIEW_THRESHOLD`` → agronomist review queue.
* below → retake guidance.

Key management is delegated to the KeyPoolManager policy (rotation on
429, quota memo, zero SDK retries) executed by the CognitionEngine
gateway. Failures from any agent propagate as
:class:`~app.agents.base.AgentFailure` (quota|busy) so the router can
answer an honest structured 429/503 — no mock verdict exists.
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

#: Stage sequences. A confirmed-by-hand crop skips vision identification;
#: the auto path identifies crop + disease in one shot, gates on the crop
#: threshold, then runs the same disease policy + advisory stages.
_PLAN_AUTO = ("diagnose_full", "policy_crop", "policy_disease", "advise")
_PLAN_USER_CROP = ("resolve_crop", "diagnose_deep", "policy_disease", "advise")


def resolve_crop_selection(selected: str, previous: list[CropCandidate]) -> tuple[str, str]:
    """Map the farmer's pick (id or label) back to the known candidate."""
    needle = selected.strip().lower()
    for candidate in previous:
        if candidate.id.lower() == needle or candidate.label.lower() == needle:
            return candidate.id, candidate.label
    from app.services.ai_vision import slugify

    return slugify(selected), selected.strip()


class _Run:
    """Mutable coordination state shared by the dispatched stages."""

    __slots__ = (
        "started", "candidates", "crop_id", "crop_label", "crop_confidence",
        "crop_source", "crop_engine", "disease", "vision_latency", "status",
        "message", "requires_crop_selection", "done", "treatment_plan",
    )

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.candidates: list[CropCandidate] = []
        self.crop_id: str | None = None
        self.crop_label: str | None = None
        self.crop_confidence: float | None = None
        self.crop_source: str | None = None
        self.crop_engine = "user"  # "user" | "gemini"
        self.disease: dict[str, Any] | None = None
        self.vision_latency: int | None = None
        self.status: str | None = None
        self.message: str = ""
        self.requires_crop_selection = False
        self.done = False
        self.treatment_plan: dict[str, Any] | None = None


class SupervisorAgent:
    """Workflow coordinator: plan → dispatch → evaluate → unified report."""

    name = "supervisor"

    def __init__(self) -> None:
        self.diagnostic = DiagnosticAgent()
        self.advisor = AdvisorAgent()

    # ==================================================================
    # The coordination loop
    # ==================================================================
    async def analyze(
        self,
        ctx: AgentContext,
        *,
        previous_candidates: list[CropCandidate] | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the pipeline and return the unified report (see module doc)."""
        settings = get_settings()
        started = time.perf_counter()
        ctx.budget_s = float(settings.AGENT_DIAGNOSTIC_BUDGET_SECONDS)

        run = _Run()
        run.started = started
        stages = _PLAN_USER_CROP if (ctx.selected_crop and ctx.selected_crop.strip()) else _PLAN_AUTO
        ctx.trace.record(
            self.name,
            "plan",
            "build stage plan from run inputs",
            "planned",
            stages=list(stages),
            reason="user_selected_crop" if stages is _PLAN_USER_CROP else "auto_identify",
        )

        early: dict[str, Any] | None = None
        for stage in stages:
            early = await self._dispatch(stage, ctx, run, previous_candidates or [], settings)
            if early is not None or run.done:
                break

        if early is not None:
            ctx.trace.record(self.name, "report", "emit unified report", early["status"],
                             ms=int((time.perf_counter() - started) * 1000))
            early["model_meta"]["trace"] = ctx.trace.rows
            return early

        report = self._final_report(
            run=run, quality_report=quality_report,
            started=started, settings=settings,
        )
        ctx.trace.record(self.name, "report", "emit unified report", report["status"],
                         ms=int((time.perf_counter() - started) * 1000))
        report["model_meta"]["trace"] = ctx.trace.rows
        return report

    # ==================================================================
    # Dispatch table: stage name → handler (returns early-report or None)
    # ==================================================================
    async def _dispatch(
        self,
        stage: str,
        ctx: AgentContext,
        run: _Run,
        previous: list[CropCandidate],
        settings: Any,
    ) -> dict[str, Any] | None:
        if stage == "resolve_crop":
            return await self._stage_resolve_crop(ctx, run, previous)
        if stage == "diagnose_full":
            return await self._stage_diagnose_full(ctx, run, settings)
        if stage == "diagnose_deep":
            return await self._stage_diagnose_deep(ctx, run, settings)
        if stage == "policy_crop":
            return self._stage_policy_crop(ctx, run, settings)
        if stage == "policy_disease":
            self._stage_policy_disease(run, settings)
            ctx.trace.record(
                self.name,
                "policy_disease",
                "apply disease confidence policy",
                str(run.status),
                confidence=(run.disease or {}).get("confidence"),
            )
        elif stage == "advise":
            await self._stage_advise(run, ctx)
        return None

    # -- stages ---------------------------------------------------------
    async def _stage_resolve_crop(
        self, ctx: AgentContext, run: _Run, previous: list[CropCandidate]
    ) -> dict[str, Any] | None:
        """Farmer confirmed the crop — resolve locally, no model needed."""
        crop_id, crop_label = resolve_crop_selection(ctx.selected_crop or "", previous)
        ctx.crop_id, ctx.crop_label = crop_id, crop_label  # for the deep shot's prompt
        run.crop_id, run.crop_label = crop_id, crop_label
        run.crop_confidence = 1.0
        run.crop_source = CropSource.USER_SELECTED.value
        run.crop_engine = "user"
        ctx.trace.record(self.name, "resolve_crop", "map farmer's pick to candidate",
                         "resolved", crop=crop_id)
        return None

    async def _stage_diagnose_full(
        self, ctx: AgentContext, run: _Run, settings: Any
    ) -> dict[str, Any] | None:
        """ONE live agent shot (candidates + deep verdict) — auto path."""
        full_analysis = await self.diagnostic.analyze_full(ctx)
        run.crop_engine = str(full_analysis.get("engine") or "gemini")
        run.vision_latency = full_analysis.get("latency_ms")
        run.candidates = _candidate_objects(full_analysis.get("candidates") or [])
        if not run.candidates:  # defensive: never 500 — ask the farmer instead
            run.candidates = [CropCandidate(id="tomato", label="Tomato", confidence=0.5)]
        top = run.candidates[0]
        run.crop_id, run.crop_label, run.crop_confidence = top.id, top.label, top.confidence
        run.crop_source = CropSource.MODEL_AUTO.value
        run.disease = full_analysis
        return None

    async def _stage_diagnose_deep(
        self, ctx: AgentContext, run: _Run, settings: Any
    ) -> dict[str, Any] | None:
        """Deep disease shot after the farmer confirmed the crop."""
        run.disease = await self.diagnostic.analyze_disease(ctx)
        run.crop_engine = str(run.disease.get("engine") or "gemini")
        run.vision_latency = run.disease.get("latency_ms")
        return None

    def _stage_policy_crop(
        self, ctx: AgentContext, run: _Run, settings: Any
    ) -> dict[str, Any] | None:
        """Threshold gate after the one-shot: continue or ask the farmer."""
        if run.crop_confidence is not None and run.crop_confidence >= settings.CROP_AUTO_THRESHOLD:
            ctx.trace.record(self.name, "policy_crop",
                             f"crop confidence {run.crop_confidence:.2f} vs "
                             f"threshold {settings.CROP_AUTO_THRESHOLD:.2f}",
                             "auto_confirmed")
            return None

        top = run.candidates[0]
        run.crop_source = CropSource.MODEL_CANDIDATES.value
        run.requires_crop_selection = True
        run.status = DiagnosisStatus.AWAITING_CROP_SELECTION.value
        run.message = (
            f"I could not identify the crop with enough confidence "
            f"(best guess: {top.label} at {top.confidence:.0%}). "
            f"Please pick one of the candidates: "
            + ", ".join(f"{c.label} ({c.confidence:.0%})" for c in run.candidates)
        )
        ctx.trace.record(self.name, "policy_crop",
                         "crop confidence below threshold", "awaiting_crop_selection",
                         confidence=top.confidence)
        # v2 contract: awaiting-crop reports carry no disease verdict
        # (nothing is persisted about a disease the farmer hasn't picked).
        run.disease = None
        return self._report(
            run=run, quality_report=None, started=run.started,
            settings=settings, stage=1,
        )

    def _stage_policy_disease(self, run: _Run, settings: Any) -> None:
        """Threshold gate for the confirmed-crop path (no early exits)."""
        self._apply_disease_policy(run, settings)

    async def _stage_advise(self, run: _Run, ctx: AgentContext) -> None:
        """AdvisorAgent grounds the localized plan (confirmed cases only)."""
        if run.status != DiagnosisStatus.CONFIRMED.value or run.disease is None:
            ctx.trace.record("advisor", "advise", "case not confirmed", "skipped",
                             status=run.status)
            return
        t0 = time.perf_counter()
        run.treatment_plan = self.advisor.build_plan(run.disease, run.crop_label).model_dump()
        ctx.trace.record("advisor", "advise", "ground localized treatment plan",
                         "planned", ms=int((time.perf_counter() - t0) * 1000),
                         source=run.treatment_plan.get("source"))

    # ==================================================================
    # Decision policy
    # ==================================================================
    def _apply_disease_policy(self, run: _Run, settings: Any) -> None:
        assert run.disease is not None
        disease = run.disease
        disease_confidence = clamp01(disease.get("confidence", 0.0))
        disease_label = str(disease.get("name_en") or "Unknown")

        if disease_confidence >= settings.DISEASE_CONFIRM_THRESHOLD:
            run.status = DiagnosisStatus.CONFIRMED.value
            run.message = (
                f"Confirmed: {disease_label} on {run.crop_label} "
                f"(confidence {disease_confidence:.0%}). Follow the treatment plan below."
            )
        elif disease_confidence >= settings.DISEASE_REVIEW_THRESHOLD:
            run.status = DiagnosisStatus.IN_REVIEW.value
            run.message = (
                f"Possible {disease_label} on {run.crop_label} "
                f"(confidence {disease_confidence:.0%}). The case was queued for "
                "agronomist review — a confirmed treatment plan follows shortly."
            )
        else:
            run.status = DiagnosisStatus.RETAKE_REQUIRED.value
            run.message = (
                f"Low confidence ({disease_confidence:.0%}) — the disease could not be "
                "identified reliably. Retake: get close to a single affected leaf, keep "
                "it flat, and use bright indirect daylight with a plain background."
            )

    # ==================================================================
    # Report assembly
    # ==================================================================
    def _final_report(
        self, *, run: _Run, quality_report: dict[str, Any] | None,
        started: float, settings: Any,
    ) -> dict[str, Any]:
        if run.status is None:  # auto path reached disease policy inline
            self._apply_disease_policy(run, settings)
        return self._report(
            run=run, quality_report=quality_report, started=started,
            settings=settings, stage=2,
        )

    @classmethod
    def _report(
        cls,
        *,
        run: _Run,
        quality_report: dict[str, Any] | None,
        started: float,
        settings: Any,
        stage: int,
    ) -> dict[str, Any]:
        """One unified, clean JSON-ready report for the router/frontend."""
        vision: dict[str, Any] = {
            "crop": run.crop_engine,
            "model": settings.vision_model_name,
            "latency_ms": run.vision_latency,
        }
        if stage == 2 and run.disease:
            vision["disease"] = str(run.disease.get("engine") or "gemini")
        meta = cls._meta(
            quality=quality_report,
            source="model",
            vision=vision,
            alternatives=(
                (run.disease or {}).get("alternatives", []) if stage == 2 else None
            ),
            started=started,
            settings=settings,
        )
        return {
            "pipeline": "supervisor_v3",
            "status": run.status,
            "stage": stage,
            "requires_crop_selection": run.requires_crop_selection,
            "message": run.message,
            "crop": {
                "id": run.crop_id,
                "label": run.crop_label,
                "confidence": run.crop_confidence,
                "source": run.crop_source,
                "engine": run.crop_engine,
            },
            "candidates": [c.model_dump() for c in run.candidates],
            "disease": run.disease,
            "treatment_plan": run.treatment_plan,
            "model_meta": meta,
        }

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
            # branches on it; v3 identity travels under `pipeline`.
            "source": source,
            "pipeline": "supervisor_v3",
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
