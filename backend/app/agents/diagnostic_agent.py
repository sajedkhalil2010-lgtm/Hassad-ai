"""DiagnosticAgent — deep visual pathology on uploaded crop images.

The v2 successor of the retired linear ``disease_detector`` service: same
proven single-shot mechanics, re-expressed as an autonomous agent.

* **Model** — ``settings.vision_model_name`` (``gemini-3.8-flash``) via
  :class:`~app.agents.key_manager.KeyPoolManager` (multi-key rotation on
  429, quota memo, zero SDK retries).
* **Rigorous JSON schema** — ``response_schema`` +
  ``response_mime_type="application/json"`` forces every verdict into the
  exact shape below: crop candidates, disease **or nutrient-deficiency**
  name (AR+EN), calibrated confidence, visible symptoms, immediate
  actions, cultural and chemical treatments, monitoring, prevention and
  differential alternatives. Nothing is ever fabricated — a failed call
  raises ``AgentFailure``.
* **One bounded shot per stage** — ``VISION_TIMEOUT_SECONDS`` (45 s).
  No retries, no loops.
* **English-first output** — content fields are English; the bilingual
  contract field ``name_ar`` is still produced for the Arabic UI.
* **Optimized payloads** — photos ≤ 256 KB pass through byte-for-byte;
  larger ones are resized to ≤ 1024 px and quality-stepped to fit a
  ~380 KB ceiling (≤ 3 bounded encodes), encoded off the event loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
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
from app.agents.key_manager import KeyPoolManager
from app.core.config import get_settings

logger = logging.getLogger("hassad.agents.diagnostic")

#: Uploads at or below this size are already optimal → forwarded verbatim.
_RAW_PASSTHROUGH_MAX = 262_144

#: Optimization targets: no photo sent to Gemini exceeds these.
_OPT_MAX_SIDE = 1024
_OPT_MAX_BYTES = 380_000


# ======================================================================
# Rigorous JSON schema enforced on the model's response
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
    """The exact shape ``gemini-3.8-flash`` must return (response_schema)."""

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


# ======================================================================
# Image preparation
# ======================================================================
def optimize_image(
    image: Image.Image,
    raw_bytes: bytes | None,
    raw_mime: str | None,
) -> tuple[bytes, str]:
    """Shrink the upload so Gemini never waits on payload size.

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
    """One shot: crop identification + deep pathology (disease/deficiency)."""
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
    """Autonomous vision pathology stage (``gemini-3.8-flash``)."""

    name = "diagnostic"

    async def analyze_full(self, ctx: AgentContext) -> dict[str, Any]:
        """ONE live shot for the whole diagnosis (crop + disease/deficiency).

        Returns ``{"candidates": [...], name_ar, name_en, confidence,
        summary, symptoms, ... , "engine": "gemini", "latency_ms": int}``.
        Raises :class:`AgentFailure` — there is no mock fallback.
        """
        settings = get_settings()
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            analysis = _coerce_full(
                await KeyPoolManager.generate_json(
                    model=settings.vision_model_name,
                    contents=await self._contents(ctx, _full_prompt()),
                    config=self._config(max_tokens=2048),
                    timeout=settings.VISION_TIMEOUT_SECONDS,
                    agent=self.name,
                )
            )
        except AgentFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - honest failure, no fabrication
            raise AgentFailure(
                f"diagnostic full stage failed: {exc}", kind="busy", agent=self.name
            ) from exc
        analysis["engine"] = "gemini"
        analysis["latency_ms"] = int((loop.time() - started) * 1000)
        return analysis

    async def analyze_disease(self, ctx: AgentContext) -> dict[str, Any]:
        """Deep live pathology after the farmer confirmed the crop."""
        settings = get_settings()
        loop = asyncio.get_running_loop()
        started = loop.time()
        crop_label = ctx.crop_label or ""
        try:
            analysis = _coerce_disease(
                await KeyPoolManager.generate_json(
                    model=settings.vision_model_name,
                    contents=await self._contents(
                        ctx, _disease_prompt(crop_label, ctx.crop_id)
                    ),
                    config=self._config(max_tokens=1024),
                    timeout=settings.VISION_TIMEOUT_SECONDS,
                    agent=self.name,
                )
            )
        except AgentFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - honest failure, no fabrication
            raise AgentFailure(
                f"diagnostic disease stage failed: {exc}", kind="busy", agent=self.name
            ) from exc
        analysis["engine"] = "gemini"
        analysis["latency_ms"] = int((loop.time() - started) * 1000)
        return analysis

    # -- internals ----------------------------------------------------
    @staticmethod
    def _config(*, max_tokens: int) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=max_tokens,
            # Rigorous JSON schema: the model MUST answer in this exact shape.
            response_mime_type="application/json",
            response_schema=(
                _VisionPayload if max_tokens >= 2048 else _DiseasePayload
            ),
            # Free-tier flash otherwise spends its budget reasoning silently.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    @staticmethod
    async def _contents(ctx: AgentContext, prompt: str) -> list[Any]:
        """[prompt, optimised image part] — encoding off the event loop."""
        from google.genai import types

        if ctx.image is None:
            raise AgentFailure("no image in agent context", agent="diagnostic")

        def _part() -> Any:
            data, mime = optimize_image(ctx.image, ctx.raw_bytes, ctx.raw_mime)
            return types.Part.from_bytes(data=data, mime_type=mime)

        return [prompt, await asyncio.to_thread(_part)]


def _coerce_full(payload: dict[str, Any]) -> dict[str, Any]:
    """Single-call payload: crop candidates + disease analysis."""
    analysis = _coerce_disease(payload)
    analysis["candidates"] = _coerce_candidates(payload)
    return analysis
