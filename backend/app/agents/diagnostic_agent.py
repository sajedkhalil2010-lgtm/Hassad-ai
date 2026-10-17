"""DiagnosticAgent — autonomous visual pathology on uploaded crop images.

v3: the agent no longer fires a linear "prompt → call → response"
wrapper. It runs a **reasoning protocol it decides by itself**:

1. **Hypothesize** — ONE structured vision pass (schema-enforced)
   produces the initial verdict: crop candidates (stage 1) and the
   disease/deficiency diagnosis with calibrated confidence, AR+EN names,
   symptoms, immediate actions, cultural/chemical treatments,
   monitoring, prevention and differential alternatives.
2. **Self-verify** (when ``AGENT_SELF_CHECK`` and confidence is inside
   the verification band) — a cheap text-only critique pass examines the
   verdict's *internal consistency* (does the summary match the named
   condition for this crop? are the symptoms coherent? are the
   treatments agronomically sensible?). Unavailable verification is
   tolerated (trace records it) — verification never blocks a live
   verdict.
3. **Refine — at most ONE bounded re-analysis** — if the critique
   finds inconsistency and confidence is still plausible, the agent
   re-examines the photo with the critique's issues injected into the
   prompt. A failed refine keeps the original (real) verdict; a
   consistent critique may nudge confidence within [0, 1].

Every step is written to the shared blackboard (``ctx.trace``) so the
supervisor can ship the full reasoning trail. All model access goes
through :class:`~app.agents.cognition.CognitionEngine` — this module
imports no SDK.

Hard bounds: ``AGENT_DIAGNOSTIC_BUDGET_SECONDS`` (90 s) covers ALL
passes combined; per-pass timeouts never undercut the shared budget;
zero retries; no mock fallback — failures raise ``AgentFailure``.

English-first output: content fields are English; the bilingual contract
field ``name_ar`` is still produced for the Arabic UI. Payloads are
optimized before transmission (≤256 KB passthrough; else ≤1024 px +
quality-stepped to ~380 KB), encoded off the event loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from PIL import Image
from pydantic import BaseModel

from app.agents.base import (
    CROP_VOCAB,
    AgentContext,
    AgentFailure,
    clamp01,
    str_list,
)
from app.agents.cognition import CognitionEngine
from app.core.config import get_settings

logger = logging.getLogger("hassad.agents.diagnostic")

#: Uploads at or below this size are already optimal → forwarded verbatim.
_RAW_PASSTHROUGH_MAX = 262_144

#: Optimization targets: no photo sent to the model exceeds these.
_OPT_MAX_SIDE = 1024
_OPT_MAX_BYTES = 380_000

#: Self-verification band: below → the retake policy will fire anyway
#: (don't waste a request), above → confidence is decisive (don't waste
#: a request either). Everything in between gets critiqued.
_VERIFY_BAND = (0.35, 0.92)

#: The agent may re-analyze at most this many times (bounded refine).
_MAX_REFINES = 1


# ======================================================================
# Rigorous JSON schema enforced on the model's responses
# ======================================================================
class _CropRow(BaseModel):
    id: str
    label: str
    confidence: float


class _DiseaseName(BaseModel):
    name_ar: str
    name_en: str
    confidence: float


class _ChemicalRow(BaseModel):
    product: str
    usage: str


class _Alternative(BaseModel):
    name_ar: str
    confidence: float


class _VisionPayload(BaseModel):
    """The exact shape the vision model must return (response_schema)."""

    candidates: list[_CropRow]
    disease: _DiseaseName
    summary: str
    symptoms: list[str]
    immediate_actions: list[str]
    cultural_treatments: list[str]
    chemical_treatments: list[_ChemicalRow]
    monitoring: list[str]
    prevention: list[str]
    alternatives: list[_Alternative]


class _DiseasePayload(BaseModel):
    """Confirmed-crop variant: deep verdict without crop candidates."""

    disease: _DiseaseName
    summary: str
    symptoms: list[str]
    immediate_actions: list[str]
    cultural_treatments: list[str]
    chemical_treatments: list[_ChemicalRow]
    monitoring: list[str]
    prevention: list[str]
    alternatives: list[_Alternative]


class _CritiquePayload(BaseModel):
    """The self-verification verdict the agent turns on itself."""

    consistent: bool
    issues: list[str]
    confidence_adjustment: float


# ======================================================================
# Image preparation
# ======================================================================
def optimize_image(
    image: Image.Image,
    raw_bytes: bytes | None,
    raw_mime: str | None,
) -> tuple[bytes, str]:
    """Shrink the upload so the model never waits on payload size.

    * Already-small photos (≤ 256 KB) are forwarded **byte-for-byte**.
    * Anything larger is resized to ≤ 1024 px per side and encoded as an
      optimised JPEG, then quality-stepped (82 → 70 → 58) and, in the
      extreme case, downscaled once more until it fits the ~380 KB byte
      ceiling. The step list is fixed and bounded — at most three encodes.

    Returns ``(bytes, mime_type)``.
    """
    if raw_bytes and len(raw_bytes) <= _RAW_PASSTHROUGH_MAX and raw_mime:
        return raw_bytes, raw_mime

    probe = image.copy()
    if probe.mode not in ("RGB", "L"):
        probe = probe.convert("RGB")  # palettes/alpha cannot encode to JPEG
    probe.thumbnail((_OPT_MAX_SIDE, _OPT_MAX_SIDE))

    def _encode(quality: int) -> bytes:
        buf = io.BytesIO()
        probe.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()

    data = _encode(82)
    for quality in (70, 58):  # bounded: at most two extra encodes
        if len(data) <= _OPT_MAX_BYTES:
            break
        data = _encode(quality)
    if len(data) > _OPT_MAX_BYTES:  # extreme case: shrink once more
        probe.thumbnail((_OPT_MAX_SIDE // 2, _OPT_MAX_SIDE // 2))
        data = _encode(58)
    return data, "image/jpeg"


# ======================================================================
# Prompts — live in-depth analysis (English-first, bilingual names)
# ======================================================================
def _full_prompt() -> str:
    """Hypothesis pass: crop identification + deep pathology, one shot."""
    vocab = ", ".join(f"{cid}={en}" for cid, en, _ar in CROP_VOCAB)
    return (
        "You are an expert crop classifier and plant pathologist inside "
        "Hassad AI, a working agricultural app. Analyse this field photo "
        "in depth — this is a REAL diagnosis from the visible symptoms, "
        "never a simulation. Rules: choose crop candidates ONLY from this "
        f"list ({vocab}) — top 3, ranked by confidence; name_en in English; "
        "name_ar in clear MSA Arabic; detect disease OR nutrient "
        "deficiency; confidence between 0.0 and 1.0; symptoms and "
        "immediate_actions 3-6 items; alternatives at most 2 (or empty); "
        "chemical_treatments: two effective fungicides/products suited to "
        "Algerian conditions with dose/usage; all other fields in concise "
        "practical English. In summary describe what you actually saw: "
        "symptom colour, shape and distribution on the leaf. If the leaf "
        "is genuinely healthy with no symptoms use name_en 'Healthy Leaf' "
        "and name_ar 'ورقة سليمة' with confidence >= 0.90."
    )


def _disease_prompt(crop_label: str, crop_id: str | None) -> str:
    host = f"{crop_label} ({crop_id})" if crop_id else crop_label
    return (
        "You are an expert plant pathologist inside Hassad AI, a working "
        f"agricultural app. The photo belongs to this crop: {host}. "
        "Give an in-depth REAL diagnosis from the visible symptoms — never "
        "a simulation. Rules: name_en in English; name_ar in clear MSA "
        "Arabic; detect disease OR nutrient deficiency; confidence 0.0-1.0; "
        "symptoms and immediate_actions 3-6 items; alternatives at most 2 "
        "(or empty); chemical_treatments: two effective products suited to "
        "Algerian conditions with dose/usage; all other fields in concise "
        "practical English. In summary describe what you actually saw: "
        "symptom colour, shape and distribution. If the leaf is genuinely "
        "healthy use name_en 'Healthy Leaf' and name_ar 'ورقة سليمة' with "
        "confidence >= 0.90."
    )


def _verify_prompt(analysis: dict[str, Any], crop_label: str | None) -> str:
    """Text-only self-critique of the agent's own verdict."""
    return (
        "You are the self-verification layer of an agricultural diagnosis "
        "agent. Critique this verdict for internal consistency — decide "
        "ONLY from what is written:\n"
        f"- crop: {crop_label or 'unspecified'}\n"
        f"- diagnosis: {analysis.get('name_en')} / {analysis.get('name_ar')} "
        f"(confidence {analysis.get('confidence')})\n"
        f"- summary: {analysis.get('summary')}\n"
        f"- symptoms: {analysis.get('symptoms')}\n"
        f"- treatments: {analysis.get('cultural_treatments')} + "
        f"{analysis.get('chemical_treatments')}\n"
        "Questions: is the summary plausible for this named condition on "
        "this crop? do the listed symptoms cohere with it? are the "
        "treatments agronomically coherent for it (no contradiction, no "
        "product mismatched to the pathogen class)? Set consistent=false "
        "and list each specific issue when anything contradicts. "
        "confidence_adjustment: a small signed correction in [-0.2, 0.2] "
        "(0.0 when nothing is wrong)."
    )


def _refine_prompt(base: str, issues: list[str]) -> str:
    """Re-analysis instruction carrying the critique's issues."""
    return (
        f"{base}\nA prior analysis of this same photo was internally "
        "inconsistent: " + "; ".join(issues[:6]) + ". Re-examine the photo "
        "carefully — verify every symptom you cite is actually visible "
        "before naming the condition."
    )


# ======================================================================
# Coercion — defensive clamps on top of the schema
# ======================================================================
def _coerce_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    import re

    rows = payload.get("candidates")
    if not isinstance(rows, list) or not rows:
        raise ValueError("no candidates in vision response")
    out: list[dict[str, Any]] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()[:80]
        cid = str(row.get("id") or "").strip().lower()
        cid = re.sub(r"[^a-z0-9-]+", "-", cid).strip("-")[:32] or "crop"
        if not label:
            continue
        out.append({"id": cid, "label": label, "confidence": clamp01(row.get("confidence", 0.5))})
    if not out:
        raise ValueError("no usable candidates in vision response")
    return out


def _coerce_disease(payload: dict[str, Any]) -> dict[str, Any]:
    disease = payload.get("disease")
    if not isinstance(disease, dict):
        raise ValueError("missing disease object")
    name_ar = str(disease.get("name_ar") or "").strip()[:160]
    name_en = str(disease.get("name_en") or "").strip()[:160]
    if not name_ar or not name_en:
        raise ValueError("missing disease name")

    chemicals: list[dict[str, str]] = []
    raw_chemicals = payload.get("chemical_treatments")
    if isinstance(raw_chemicals, list):
        for row in raw_chemicals[:5]:
            if isinstance(row, dict):
                product = str(row.get("product") or "").strip()[:200]
                usage = str(row.get("usage") or row.get("dose") or "").strip()[:240]
                if product:
                    chemicals.append({"product": product, "usage": usage})
            elif isinstance(row, str) and row.strip():
                chemicals.append({"product": row.strip()[:200], "usage": ""})

    alternatives: list[dict[str, Any]] = []
    raw_alts = payload.get("alternatives")
    if isinstance(raw_alts, list):
        for row in raw_alts[:2]:
            if isinstance(row, dict):
                alt = str(row.get("name_ar") or "").strip()[:160]
                if alt:
                    alternatives.append(
                        {"name_ar": alt, "confidence": clamp01(row.get("confidence", 0.3))}
                    )

    return {
        "name_ar": name_ar,
        "name_en": name_en,
        "confidence": clamp01(disease.get("confidence", 0.5)),
        "summary": str(payload.get("summary") or "").strip()[:500],
        "symptoms": str_list(payload, "symptoms", lo=1, hi=6),
        "immediate_actions": str_list(payload, "immediate_actions", lo=1, hi=6),
        "cultural_treatments": str_list(payload, "cultural_treatments", lo=1, hi=6),
        "chemical_treatments": chemicals,
        "monitoring": str_list(payload, "monitoring", lo=1, hi=4),
        "prevention": str_list(payload, "prevention", lo=1, hi=4),
        "alternatives": alternatives,
    }


# ======================================================================
# The agent
# ======================================================================
class DiagnosticAgent:
    """Autonomous vision pathology agent (hypothesize → verify → refine)."""

    name = "diagnostic"

    async def analyze_full(self, ctx: AgentContext) -> dict[str, Any]:
        """Full protocol: crop candidates + deep verdict in one run.

        Returns ``{"candidates": [...], name_ar, name_en, confidence,
        summary, ... , "engine": "gemini", "latency_ms": int}``.
        Raises :class:`AgentFailure` — there is no mock fallback.
        """
        return await self._run(ctx, full=True)

    async def analyze_disease(self, ctx: AgentContext) -> dict[str, Any]:
        """Full protocol after the farmer confirmed the crop (deep shot)."""
        return await self._run(ctx, full=False)

    # -- the reasoning protocol -----------------------------------------
    async def _run(self, ctx: AgentContext, *, full: bool) -> dict[str, Any]:
        settings = get_settings()
        trace = ctx.trace
        loop = asyncio.get_running_loop()
        run_started = loop.time()
        budget = float(ctx.budget_s or settings.AGENT_DIAGNOSTIC_BUDGET_SECONDS)
        base_prompt = _full_prompt() if full else _disease_prompt(
            ctx.crop_label or "", ctx.crop_id
        )
        coerce = _coerce_full if full else _coerce_disease
        schema = _VisionPayload if full else _DiseasePayload

        # Stage 1 — hypothesis: ONE structured vision pass.
        t0 = loop.time()
        try:
            analysis = coerce(
                await CognitionEngine.structured_json(
                    model=settings.vision_model_name,
                    contents=await self._contents(ctx, base_prompt),
                    config=self._config(schema, max_tokens=2048 if full else 1024),
                    timeout=max(min(budget, settings.VISION_TIMEOUT_SECONDS), 10.0),
                    agent=self.name,
                )
            )
        except AgentFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - honest failure, no fabrication
            raise AgentFailure(
                f"diagnostic hypothesis failed: {exc}", kind="busy", agent=self.name
            ) from exc
        trace.record(
            self.name,
            "hypothesis",
            "structured vision pass",
            "verdict",
            ms=int((loop.time() - t0) * 1000),
            confidence=analysis["confidence"],
            diagnosis=analysis["name_en"],
        )

        # Stage 2 — self-verification (optional per config/band).
        if not settings.AGENT_SELF_CHECK:
            trace.record(self.name, "self_verify", "config disabled", "skipped")
        elif not (_VERIFY_BAND[0] <= analysis["confidence"] <= _VERIFY_BAND[1]):
            trace.record(
                self.name,
                "self_verify",
                f"confidence {analysis['confidence']:.2f} outside band",
                "skipped",
            )
        else:
            analysis = await self._verify_and_maybe_refine(
                ctx=ctx,
                analysis=analysis,
                base_prompt=base_prompt,
                schema=schema,
                coerce=coerce,
                run_started=run_started,
                budget=budget,
            )

        analysis["engine"] = "gemini"
        analysis["latency_ms"] = int((loop.time() - run_started) * 1000)
        return analysis

    async def _verify_and_maybe_refine(
        self,
        *,
        ctx: AgentContext,
        analysis: dict[str, Any],
        base_prompt: str,
        schema: type[BaseModel],
        coerce: Any,
        run_started: float,
        budget: float,
    ) -> dict[str, Any]:
        """Critique the verdict; refine at most once. Never blocks."""
        settings = get_settings()
        trace = ctx.trace
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - run_started
        remaining = budget - elapsed

        # Text-only critique of the verdict already in hand.
        t0 = loop.time()
        try:
            critique = await CognitionEngine.structured_json(
                model=settings.GEMINI_MODEL_NAME,
                contents=_verify_prompt(analysis, ctx.crop_label),
                config=self._config(_CritiquePayload, max_tokens=768),
                timeout=max(min(remaining, 30.0), 8.0),
                agent=self.name,
            )
        except AgentFailure as exc:
            # Verification is best-effort: an unavailable self-check must
            # never destroy a real, live verdict (robust recovery).
            trace.record(
                self.name,
                "self_verify",
                "critique own verdict",
                "unavailable",
                ms=int((loop.time() - t0) * 1000),
                reason=exc.kind,
            )
            return analysis
        except Exception as exc:  # noqa: BLE001 - malformed critique etc.
            trace.record(
                self.name,
                "self_verify",
                "critique own verdict",
                "unavailable",
                ms=int((loop.time() - t0) * 1000),
                reason="parse_error",
                detail=str(exc)[:120],
            )
            return analysis

        consistent = bool(critique.get("consistent", True))
        issues = [
            s for s in (critique.get("issues") or []) if isinstance(s, str)
        ][:6]
        try:
            adjustment = float(critique.get("confidence_adjustment") or 0.0)
        except (TypeError, ValueError):
            adjustment = 0.0
        adjustment = max(-0.2, min(0.2, adjustment))
        trace.record(
            self.name,
            "self_verify",
            "critique own verdict",
            "consistent" if consistent else "inconsistent",
            ms=int((loop.time() - t0) * 1000),
            issues=len(issues),
        )

        # Stage 3 — bounded refine: at most ONE re-analysis.
        if not consistent and analysis["confidence"] >= 0.45 and _MAX_REFINES >= 1:
            t0 = loop.time()
            try:
                refined = coerce(
                    await CognitionEngine.structured_json(
                        model=settings.vision_model_name,
                        contents=await self._contents(
                            ctx, _refine_prompt(base_prompt, issues)
                        ),
                        config=self._config(schema, max_tokens=2048),
                        timeout=max(budget - (loop.time() - run_started), 8.0),
                        agent=self.name,
                    )
                )
                trace.record(
                    self.name,
                    "refine",
                    "re-examine photo with critique issues",
                    "refined",
                    ms=int((loop.time() - t0) * 1000),
                    confidence=refined["confidence"],
                    diagnosis=refined["name_en"],
                )
                return refined
            except AgentFailure as exc:
                # Keep the original real verdict — refinement is optional.
                trace.record(
                    self.name,
                    "refine",
                    "re-examine photo with critique issues",
                    "kept_original",
                    ms=int((loop.time() - t0) * 1000),
                    reason=exc.kind,
                )
                return analysis
            except Exception as exc:  # noqa: BLE001 - malformed refinement
                trace.record(
                    self.name,
                    "refine",
                    "re-examine photo with critique issues",
                    "kept_original",
                    ms=int((loop.time() - t0) * 1000),
                    reason="parse_error",
                    detail=str(exc)[:120],
                )
                return analysis

        if consistent and abs(adjustment) >= 0.01:
            analysis["confidence"] = clamp01(analysis["confidence"] + adjustment)
            trace.record(
                self.name,
                "confidence",
                "apply self-check adjustment",
                "adjusted",
                delta=round(adjustment, 3),
                confidence=analysis["confidence"],
            )
        return analysis

    # -- internals ----------------------------------------------------
    @staticmethod
    def _config(schema: type[BaseModel], *, max_tokens: int) -> Any:
        return CognitionEngine.config(schema, temperature=0.15, max_tokens=max_tokens)

    @staticmethod
    async def _contents(ctx: AgentContext, prompt: str) -> list[Any]:
        """[prompt, optimised image part] — encoding off the event loop."""
        if ctx.image is None:
            raise AgentFailure("no image in agent context", agent="diagnostic")

        def _part() -> Any:
            data, mime = optimize_image(ctx.image, ctx.raw_bytes, ctx.raw_mime)
            return CognitionEngine.image_part(data, mime)

        return [prompt, await asyncio.to_thread(_part)]


def _coerce_full(payload: dict[str, Any]) -> dict[str, Any]:
    """Single-call payload: crop candidates + disease analysis."""
    analysis = _coerce_disease(payload)
    analysis["candidates"] = _coerce_candidates(payload)
    return analysis
