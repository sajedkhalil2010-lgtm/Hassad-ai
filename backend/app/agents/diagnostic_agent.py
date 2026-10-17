"""DiagnosticAgent — fully autonomous LOCAL pathology analysis agent.

100% local pipeline (no external APIs, no network, no rate limits):

1. **Quantitative vision** — ``local_vision.analyze_leaf`` segments the
   leaf and its lesions with OpenCV + NumPy and returns measured metrics
   (``affected_area_percentage``, ``segmentation_dice_score``, severity,
   lesion geometry, colour-family fractions, halo index …).
2. **Condition matching** — a deterministic rules engine compares the
   measured feature vector against Tébessa-relevant condition profiles
   (early blight, late blight, septoria, rust, powdery mildew, nutrient
   deficiencies, healthy …) and produces scored candidates.
3. **Confidence** — the reported confidence is a *real* number:
   condition-fit score scaled by the segmentation consensus (Dice) and
   the ambiguity margin. No fabrication, no mock verdicts.

The agent exposes the same two entry points the SupervisorAgent's
coordination loop dispatches (``analyze_full`` for the auto path —
candidates + verdict — and ``analyze_disease`` for the confirmed-crop
deep pass). Every step is appended to the shared ``RunTrace``
blackboard for observability.

English-first formatting: keys, statuses and messages are English;
bilingual ``(en, ar)`` symptom rows travel with the verdict.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.agents.base import (
    CROP_VOCAB,
    AgentContext,
    AgentFailure,
    RunTrace,
)
from app.agents.local_vision import VisionMetrics, analyze_leaf_async

logger = logging.getLogger("hassad.agents.diagnostic")

_ENGINE = "local_cv"
_PIPELINE_TAG = "opencv-numpy-local"

_CROP_AR = {cid: ar for cid, _en, ar in CROP_VOCAB}
_CROP_EN = {cid: en for cid, en, _ar in CROP_VOCAB}

# Regional prior (Tébessa belt) used only to seed the crop-selection
# candidates when the farmer has not confirmed a crop yet. The local
# engine cannot classify crop species from a leaf photo — the honest
# answer is to ask, and these priors order the choices.
REGIONAL_PRIOR: tuple[str, ...] = (
    "wheat", "barley", "olive", "tomato", "potato", "grape", "date-palm", "maize",
)


# ======================================================================
# Condition profiles — Tébessa-relevant disease / disorder heuristics
# ======================================================================
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class ConditionProfile:
    """A named scoring function over the measured VisionMetrics.

    ``fit()`` returns 0..1: how well the observed features match the
    condition's visual signature. ``crops`` restricts the profile
    (None = any crop); a mismatch penalises but never zeroes the fit
    (symptoms overlap across species).
    """

    __slots__ = ("cond_id", "name_en", "name_ar", "crops", "fit", "summary_en")

    def __init__(self, cond_id, name_en, name_ar, crops, fit, summary_en):
        self.cond_id = cond_id
        self.name_en = name_en
        self.name_ar = name_ar
        self.crops = crops  # tuple[str, ...] | None
        self.fit = fit
        self.summary_en = summary_en


def _fit_early_blight(m: VisionMetrics) -> float:
    f = m.color_fractions
    nec = f.get("necrotic", 0.0)
    if nec <= 0.01:
        return 0.0
    lesion_total = max(sum(f.values()), 1e-6)
    score = 0.30 + 0.40 * _clamp(nec / lesion_total) + 0.35 * _clamp(m.halo_index / 0.5)
    if 3 <= m.lesion_count <= 80:
        score += 0.10
    return _clamp(score)


def _fit_late_blight(m: VisionMetrics) -> float:
    f = m.color_fractions
    dark = f.get("water_soaked", 0.0)
    nec = f.get("necrotic", 0.0)
    if dark + nec <= 0.01:
        return 0.0
    lesion_total = max(sum(f.values()), 1e-6)
    score = 0.25 + 0.45 * _clamp(dark / lesion_total) + 0.20 * _clamp(m.largest_lesion_share / 0.5)
    if m.affected_area_percentage > 12:
        score += 0.10
    return _clamp(score)


def _fit_septoria(m: VisionMetrics) -> float:
    f = m.color_fractions
    nec = f.get("necrotic", 0.0)
    if nec <= 0.01:
        return 0.0
    lesion_total = max(sum(f.values()), 1e-6)
    score = 0.25 + 0.35 * _clamp(nec / lesion_total) + 0.15 * _clamp(m.halo_index / 0.4)
    if m.lesion_count >= 12:
        score += 0.20
    if m.lesion_count >= 30:
        score += 0.10
    return _clamp(score)


def _fit_rust(m: VisionMetrics) -> float:
    f = m.color_fractions
    rust = f.get("rust_ochre", 0.0)
    if rust <= 0.005:
        return 0.0
    lesion_total = max(sum(f.values()), 1e-6)
    score = 0.30 + 0.45 * _clamp(rust / lesion_total)
    if m.lesion_count >= 10:
        score += 0.15
    return _clamp(score)


def _fit_powdery(m: VisionMetrics) -> float:
    powd = m.color_fractions.get("powdery", 0.0)
    if powd <= 0.005:
        return 0.0
    return _clamp(0.35 + 0.50 * _clamp(powd / 0.10))


def _fit_deficiency_n(m: VisionMetrics) -> float:
    f = m.color_fractions
    chl = f.get("chlorotic", 0.0)
    nec = f.get("necrotic", 0.0)
    if chl <= 0.02:
        return 0.0
    # generalized yellowing with little necrotic spotting → N hunger
    return _clamp(0.30 + 0.40 * _clamp(chl / 0.15) - 0.35 * _clamp(nec / 0.05))


def _fit_deficiency_k(m: VisionMetrics) -> float:
    nec = m.color_fractions.get("necrotic", 0.0)
    if nec <= 0.01 or m.border_necrosis_share <= 0.2:
        return 0.0
    return _clamp(0.30 + 0.45 * _clamp(m.border_necrosis_share / 0.8))


def _fit_healthy(m: VisionMetrics) -> float:
    if m.affected_area_percentage > 0.6:
        return 0.0
    return _clamp(0.60 + 0.40 * (1.0 - _clamp(m.affected_area_percentage / 0.6)))


PROFILES: tuple[ConditionProfile, ...] = (
    ConditionProfile(
        "early_blight", "Early Blight", "اللفحة المبكرة",
        ("tomato", "potato", "pepper", "eggplant"),
        _fit_early_blight,
        "Necrotic lesions with chlorotic halos (Alternaria-type pattern)",
    ),
    ConditionProfile(
        "late_blight", "Late Blight", "اللفحة المتأخرة",
        ("tomato", "potato"),
        _fit_late_blight,
        "Dark water-soaked coalescing lesions (oomycete blight pattern)",
    ),
    ConditionProfile(
        "septoria_leaf_spot", "Septoria Leaf Spot", "اللفحة الدائرية",
        ("tomato", "wheat"),
        _fit_septoria,
        "Many small circular necrotic spots",
    ),
    ConditionProfile(
        "leaf_rust", "Leaf Rust", "الصدأ",
        ("wheat", "barley", "grape"),
        _fit_rust,
        "Orange-ochre rust pustules on foliage",
    ),
    ConditionProfile(
        "powdery_mildew", "Powdery Mildew", "البَيَاض الدقيقي",
        ("grape", "wheat", "tomato", "potato", "date-palm"),
        _fit_powdery,
        "Bright powdery surface colonization",
    ),
    ConditionProfile(
        "nitrogen_deficiency", "Nitrogen Deficiency", "نقص النيتروجين",
        None,
        _fit_deficiency_n,
        "Generalized chlorosis without necrotic spotting",
    ),
    ConditionProfile(
        "potassium_deficiency", "Potassium Deficiency", "نقص البوتاسيوم",
        None,
        _fit_deficiency_k,
        "Marginal necrotic burn (border necrosis)",
    ),
    ConditionProfile(
        "healthy_leaf", "Healthy Leaf", "ورقة سليمة",
        None,
        _fit_healthy,
        "No measurable lesion pressure detected",
    ),
)


def _match_conditions(m: VisionMetrics, crop: str | None) -> list[dict[str, Any]]:
    """Score all profiles → ranked candidates with honest confidences."""
    scored: list[tuple[float, ConditionProfile]] = []
    for prof in PROFILES:
        fit = prof.fit(m)
        if fit <= 0.0:
            continue
        if prof.crops is not None and crop is not None and crop not in prof.crops:
            fit *= 0.55  # penalty, not a veto (symptoms overlap species)
        scored.append((fit, prof))
    scored.sort(key=lambda t: t[0], reverse=True)

    if not scored:
        return [{
            "id": "uncertain_condition",
            "name_en": "Uncertain Spot Pattern",
            "name_ar": "نمط بقع غير مؤكد",
            "confidence": 0.10,
            "summary": "Lesion pixels are present but no condition profile matched.",
        }]

    # confidence = fit × segmentation consensus × ambiguity margin
    margin = 1.0
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < 0.12:
        margin = 0.80
    out: list[dict[str, Any]] = []
    for fit, prof in scored[:3]:
        conf = round(_clamp(fit * (0.55 + 0.45 * m.segmentation_dice_score) * margin), 2)
        out.append({
            "id": prof.cond_id,
            "name_en": prof.name_en,
            "name_ar": prof.name_ar,
            "confidence": conf,
            "summary": prof.summary_en,
        })
    return out


# ======================================================================
# Bilingual symptom templates (measured, not invented)
# ======================================================================
_SYM_NECROTIC = (
    "{n} necrotic (brown) lesions detected across the leaf surface",
    "تم رصد {n} بقعة بنية ميتة على سطح الورقة",
)
_SYM_AREA = (
    "Measured affected area: {pct}% of the leaf surface",
    "المساحة المصابة المقاسة: {pct}% من سطح الورقة",
)
_SYM_HALO = (
    "Yellow chlorotic halos around lesion cores",
    "أطراف صفراء حول تجمعات النقاط",
)
_SYM_WATER = (
    "Dark water-soaked patches (blight-type tissue collapse)",
    "بقع داكنة مبتلّة (انسجام نسيجي من نوع اللفحة)",
)
_SYM_POWDERY = (
    "Bright powdery surface growth (mildew-type colonization)",
    "نمو أبيض مغبر على السطح (من نوع الدقيقي)",
)
_SYM_RUST = (
    "Orange-ochre pustules (rust-type fruiting bodies)",
    "بثّات برتقالية مائلة للحمرة (من نوع الصدأ)",
)
_SYM_MARGINAL = (
    "Marginal necrotic burn along the leaf edges",
    "احتراق محيطي (موت نسيجي) على حواف الورقة",
)
_SYM_CHLOROSIS = (
    "Generalized yellowing (chlorosis) between the veins",
    "اصفرار عام بين العروق",
)
_SYM_HEALTHY = (
    "No visible lesions — foliage measures as healthy",
    "لا توجد بقع ظاهرة — الأوراق تُقاس كسليمة",
)


def _symptoms(m: VisionMetrics) -> list[dict[str, str]]:
    """Bilingual symptom rows derived from the measured metrics."""
    f = m.color_fractions
    rows: list[dict[str, str]] = []
    if m.lesion_count:
        rows.append({"en": _SYM_NECROTIC[0].format(n=m.lesion_count),
                     "ar": _SYM_NECROTIC[1].format(n=m.lesion_count)})
        rows.append({"en": _SYM_AREA[0].format(pct=m.affected_area_percentage),
                     "ar": _SYM_AREA[1].format(pct=m.affected_area_percentage)})
    if f.get("necrotic", 0.0) > 0.01 and m.halo_index > 0.15:
        rows.append({"en": _SYM_HALO[0], "ar": _SYM_HALO[1]})
    if f.get("water_soaked", 0.0) > 0.01:
        rows.append({"en": _SYM_WATER[0], "ar": _SYM_WATER[1]})
    if f.get("powdery", 0.0) > 0.01:
        rows.append({"en": _SYM_POWDERY[0], "ar": _SYM_POWDERY[1]})
    if f.get("rust_ochre", 0.0) > 0.005:
        rows.append({"en": _SYM_RUST[0], "ar": _SYM_RUST[1]})
    if m.border_necrosis_share > 0.3 and f.get("necrotic", 0.0) > 0.01:
        rows.append({"en": _SYM_MARGINAL[0], "ar": _SYM_MARGINAL[1]})
    if f.get("chlorotic", 0.0) > 0.03 and f.get("necrotic", 0.0) <= 0.02:
        rows.append({"en": _SYM_CHLOROSIS[0], "ar": _SYM_CHLOROSIS[1]})
    if not rows:
        rows.append({"en": _SYM_HEALTHY[0], "ar": _SYM_HEALTHY[1]})
    return rows


# ======================================================================
# The agent
# ======================================================================
class DiagnosticAgent:
    """Autonomous local diagnostic agent (crop resolution + disease pass)."""

    name = "diagnostic"

    async def analyze_full(self, ctx: AgentContext) -> dict[str, Any]:
        """Auto path: one local pass producing candidates + verdict."""
        analysis = await self._analyze_local(ctx)
        crop = (ctx.crop_id or "").strip() or None
        if crop:
            candidates = [_user_crop_candidate(crop)]
        else:
            candidates = _regional_candidates()
        analysis["candidates"] = candidates
        analysis["crop_resolved"] = bool(crop)
        return analysis

    async def analyze_disease(self, ctx: AgentContext) -> dict[str, Any]:
        """Confirmed-crop path: deep disease pass for the chosen crop."""
        return await self._analyze_local(ctx)

    # ------------------------------------------------------------------
    async def _analyze_local(self, ctx: AgentContext) -> dict[str, Any]:
        started = time.perf_counter()
        if ctx.image is None:
            raise AgentFailure("vision_processing", "No image was provided to the agent.")
        try:
            metrics: VisionMetrics = await analyze_leaf_async(ctx.image)
        except Exception as exc:  # segmentation impossible
            logger.warning("local vision failed: %s", exc)
            raise AgentFailure(
                "vision_processing", "The image could not be analysed locally."
            ) from exc

        ctx.trace.record(
            self.name, "local_segment",
            "segment leaf + lesions (OpenCV/NumPy)",
            f"{metrics.lesion_count} lesion(s), {metrics.affected_area_percentage}% area",
            ms=metrics.latency_ms,
            mode=metrics.mode,
            dice=metrics.segmentation_dice_score,
        )

        crop = (ctx.crop_id or "").strip() or None
        candidates = _match_conditions(metrics, crop)
        top = candidates[0]

        analysis: dict[str, Any] = {
            "condition_id": top["id"],
            "name_en": top["name_en"],
            "name_ar": top["name_ar"],
            "confidence": top["confidence"],
            "summary": top["summary"],
            "symptoms": _symptoms(metrics),
            "alternatives": candidates[1:],
            "quantitative": metrics.as_dict(),
            "affected_area_percentage": metrics.affected_area_percentage,
            "severity": metrics.severity,
            "engine": _ENGINE,
            "pipeline": _PIPELINE_TAG,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        ctx.trace.record(
            self.name, "condition_match",
            "score condition profiles against measured features",
            f"{top['name_en']} @ {top['confidence']:.2f}",
            alternatives=[a["name_en"] for a in analysis["alternatives"]],
        )
        return analysis


def _regional_candidates() -> list[dict[str, Any]]:
    out = []
    for cid in REGIONAL_PRIOR[:3]:
        out.append({
            "id": cid,
            "label": _CROP_EN.get(cid, cid),
            "label_ar": _CROP_AR.get(cid, cid),
            # prior only — the local engine cannot classify crop species
            # from a leaf photo; the farmer decides. Kept far below the
            # auto-confirm threshold on purpose.
            "confidence": 0.35,
            "source": "regional_prior",
        })
    return out


def _user_crop_candidate(crop: str) -> dict[str, Any]:
    cid = crop if crop in _CROP_EN else f"custom:{crop}"
    return {
        "id": cid,
        "label": _CROP_EN.get(crop, crop),
        "label_ar": _CROP_AR.get(crop, crop),
        "confidence": 1.0,
        "source": "user_selected",
    }
