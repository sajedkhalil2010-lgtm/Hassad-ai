"""disease_detector — live Google Gemini 3.8 Flash vision for crop pathology.

LIVE-ONLY engine: every uploaded photo is analysed by ``gemini-3.8-flash``
in one bounded round trip. There are **no mock/simulated verdicts and no
static "healthy leaf" defaults** — if the model cannot answer (congested
tier, network, malformed payload) the pipeline raises ``VisionUnavailable``
and the API replies with a structured, retryable 503 instead of
fabricating a diagnosis.

Contract
--------
* **Model** — ``settings.vision_model_name`` (default ``gemini-3.8-flash``).
* **Key** — ``settings.gemini_api_keys``: ``GEMINI_API_KEY_1..4`` in
  order, then the base ``GEMINI_API_KEY`` (each field honours env-var >
  ``.env`` > default, so ``python -m dotenv run`` and pydantic-settings
  agree). Values are never logged or returned to any client; a 429 on
  one key fails over to the next.
* **Image bytes** — the payload is optimised before every call so the
  model never waits on bytes: photos already ≤ 256 KB are forwarded
  byte-for-byte, anything larger is resized to ≤ 1024 px per side and
  quality-stepped down until it fits a ~380 KB ceiling (at most three
  bounded encodes — no unbounded loops). This removes upload-side stalls
  that used to eat the deadline.
* **Rigorous JSON schema** — ``response_schema`` +
  ``response_mime_type="application/json"`` forces the model to return
  the pathology (AR+EN name, calibrated confidence), visible symptoms,
  immediate actions, cultural and chemical treatments — all in Arabic —
  as a schema-valid object every time.
* **One shot, generous deadline** — ``VISION_TIMEOUT_SECONDS`` (default
  45s, tunable 10-60s) per stage. Google's free tier routinely needs
  20-40s under load, so the budget never cuts a healthy answer short.
  No retries, no loops; failure raises ``VisionUnavailable`` (never a
  fake answer) and the caller converts it into an honest, retryable 503.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from typing import Any

from PIL import Image
from pydantic import BaseModel

from app.core.config import get_settings
from app.schemas.diagnosis import CropCandidate, TreatmentPlan
from app.services.ai_vision import build_treatment_plan

logger = logging.getLogger("hassad.vision")

# (id, English name, Arabic name) — canonical crop vocabulary; Gemini is
# instructed to choose from this list so selection ids stay stable.
CROP_VOCAB: tuple[tuple[str, str, str], ...] = (
    ("tomato", "Tomato", "طماطم"),
    ("potato", "Potato", "بطاطس"),
    ("wheat", "Wheat", "قمح"),
    ("rice", "Rice", "أرز"),
    ("maize", "Maize", "ذرة"),
    ("cotton", "Cotton", "قطن"),
    ("grape", "Grape", "عنب"),
    ("soybean", "Soybean", "فول صويا"),
    ("barley", "Barley", "شعير"),
    ("date-palm", "Date Palm", "نخيل"),
)

#: Uploads at or below this size are already optimal → forwarded verbatim.
_RAW_PASSTHROUGH_MAX = 262_144

#: Optimisation targets: no photo sent to Gemini exceeds these.
_OPT_MAX_SIDE = 1024
_OPT_MAX_BYTES = 380_000

#: mime lookup for the validated upload formats.
_RAW_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def raw_mime_for(image_format: str) -> str:
    """MIME type for a validated upload format (``JPEG``/``PNG``/``WEBP``)."""
    return _RAW_MIME.get(str(image_format or "").upper(), "image/jpeg")


class VisionUnavailable(RuntimeError):
    """Gemini could not deliver a live verdict within the bounded attempt.

    Raised (never swallowed) so the API can answer with a structured,
    retryable 503 — the pipeline never fabricates a diagnosis.

    ``kind`` distinguishes the two honest failure modes so the farmer
    gets *actionable* Arabic guidance: ``"quota"`` = the free-tier daily
    allowance ran out (retry after the reset window), ``"busy"`` =
    congestion/timeout within the attempt budget (retry in moments).
    """

    def __init__(self, message: str, *, kind: str = "busy") -> None:
        super().__init__(message)
        self.kind = kind if kind in ("quota", "busy") else "busy"


def _failure_kind(exc: Exception) -> str:
    """Classify an upstream failure from the SDK error payload."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text or "quota" in text.lower():
        return "quota"
    return "busy"


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
def _optimize_image(
    image: Image.Image,
    raw_bytes: bytes | None,
    raw_mime: str | None,
) -> tuple[bytes, str]:
    """Shrink the upload so Gemini never waits on payload size.

    * Already-small photos (≤ 256 KB) are forwarded **byte-for-byte**
      (previous behaviour preserved where it costs nothing).
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


def _image_part(image: Image.Image, raw_bytes: bytes | None, raw_mime: str | None) -> Any:
    """Optimised payload for the multimodal call (see ``_optimize_image``)."""
    from google.genai import types

    data, mime = _optimize_image(image, raw_bytes, raw_mime)
    return types.Part.from_bytes(data=data, mime_type=mime)


# ======================================================================
# Prompts — live in-depth analysis, strictly Arabic output
# ======================================================================
def _full_prompt() -> str:
    """One shot: crop identification + deep pathology + Arabic plan."""
    vocab = "، ".join(f"{cid}: {ar}" for cid, _en, ar in CROP_VOCAB)
    return (
        "أنت مصنّف محاصيل ومهندس أمراض نباتات محترف داخل تطبيق «حَصاد». "
        "حلّل هذه الصورة الزراعية بعمق علمي واحد — هذه ليست محاكاة بل تشخيص حقيقي "
        "يستند إلى الأعراض المرئية في الصورة فعلياً. "
        f"القواعد: اختر المرشحين من هذه القائمة حصراً ({vocab}) — 3 مرشحين مرتّبين "
        "من الأعلى ثقةً؛ name_en بالإنجليزية؛ confidence بين 0.0 و1.0؛ "
        "symptoms وimmediate_actions من 3 إلى 6 عناصر؛ alternatives عنصران كحدّ "
        "أقصى أو قائمة فارغة؛ chemical_treatments مادتان فطريات فعالتان وفق توجيه "
        "الظروف المحلية في الجزائر؛ كل النصوص بالعربية الفصحى الإجرائية الدقيقة. "
        "صف في summary مرضراً ما رأيته بالفعل: لون الأعراض وشكلها وتوزيعها على الورقة. "
        'إن كانت الورقة سليمة حقيقةً بلا أي أعراض استخدم name_ar "ورقة سليمة" '
        'وname_en "Healthy Leaf" وconfidence ≥ 0.90.'
    )


def _disease_prompt(crop_label: str, crop_id: str | None) -> str:
    host = f"{crop_label} ({crop_id})" if crop_id else crop_label
    return (
        "أنت مهندس أمراض نباتات محترف داخل تطبيق «حَصاد». "
        f"حلّل هذه الصورة الزراعية بعمق علمي. المحصول الذي تعود إليه الصورة: {host}. "
        "هذه ليست محاكاة — تشخيص حقيقي من الأعراض المرئية في الصورة. "
        "القواعد: كل النصوص بالعربية الفصحى الإجرائية الدقيقة؛ name_en بالإنجليزية؛ "
        "confidence بين 0.0 و1.0؛ symptoms وimmediate_actions من 3 إلى 6 عناصر؛ "
        "alternatives عنصران كحدّ أقصى أو قائمة فارغة؛ chemical_treatments مادتان "
        "فطريات فعالتان وفق توجيه الظروف المحلية في الجزائر. "
        "صف في summary مرضراً ما رأيته فعلاً: لون الأعراض وشكلها وتوزيعها. "
        'إن كانت الورقة سليمة حقيقةً بلا أي أعراض استخدم name_ar "ورقة سليمة" '
        'وname_en "Healthy Leaf" وconfidence ≥ 0.90.'
    )


# ======================================================================
# Key failover + zero-retry policy (quota-safe)
# ======================================================================
#: In-process quota memo: chain position → monotonic time when Google's
#: own reset window elapses. A key known to be exhausted is skipped until
#: then, so background requests cannot keep firing at a spent bucket; when
#: every key is blocked we answer instantly with ZERO network calls.
_KEY_BLOCKED_UNTIL: dict[int, float] = {}


def _quota_wait_seconds(exc: Exception) -> int | None:
    """Extract Google's own reset window from a 429 payload.

    Handles both shapes Google sends: ``"Please retry in 13h17m20.3s"``
    (message) and ``"retryDelay": "47840s"`` (details); ``None`` if absent.
    """
    text = str(exc)
    match = re.search(r"retry in (\d+)h(\d+)m", text)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60
    match = re.search(r"retryDelay[\"']?\s*:\s*[\"']?(\d+)s", text)
    if match:
        return int(match.group(1))
    match = re.search(r"retry in (\d+)s", text)
    if match:
        return int(match.group(1))
    return None


def _key_available(position: int) -> bool:
    """True when the key at this chain position has not spent its quota."""
    return time.monotonic() >= _KEY_BLOCKED_UNTIL.get(position, 0.0)


def _mark_key_exhausted(position: int, exc: Exception) -> None:
    wait = min(_quota_wait_seconds(exc) or 600, 24 * 3600)
    _KEY_BLOCKED_UNTIL[position] = time.monotonic() + wait
    # Position + window only — never any key material.
    logger.warning("vision key #%d quota-limited — skipping for ~%ds", position + 1, wait)


# ======================================================================
# The bounded Gemini call (one attempt per key, 429 failover)
# ======================================================================
def _parse_vision_response(response: Any) -> dict[str, Any]:
    """Schema-validated payload from the SDK, or a manual JSON fallback."""
    # Preferred: the SDK already validated the body against the schema.
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BaseModel):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed

    # Safety net: parse the raw JSON text ourselves.
    text = (response.text or "").strip()
    if not text:
        raise ValueError("empty vision response")
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"non-JSON vision response: {text[:80]!r}")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("vision JSON is not an object")
    return payload


async def _gemini_json(
    image: Image.Image,
    prompt: str,
    *,
    max_tokens: int = 1024,
    schema: type[BaseModel],
    raw_bytes: bytes | None = None,
    raw_mime: str | None = None,
) -> dict[str, Any]:
    """Bounded Gemini vision call: one attempt per key, 429 failover.

    Key chain — ``settings.gemini_api_keys``: ``GEMINI_API_KEY_1..4``
    first (the operator's primary + backups in ``.env``), base
    ``GEMINI_API_KEY`` last; each field honours env-var > .env > default,
    values never logged or exposed.

    Per key, exactly **ONE** HTTP attempt: the google-genai SDK's
    built-in retries are disabled (``HttpRetryOptions(attempts=0)``), so
    a 429 can never be hammered in a hidden backoff loop (the default is
    up to 4 retries on 429/5XX). A quota answer fails over to the next
    key and memoises this one until Google's reset window; any other
    failure raises immediately. When every key is blocked the call fails
    **instantly without a single network request**.

    Raises on any problem; callers map failures to ``VisionUnavailable``.
    """
    settings = get_settings()
    chain = settings.gemini_api_keys
    if not chain:
        raise RuntimeError("gemini_not_configured")

    from google import genai  # imported lazily: optional at runtime
    from google.genai import types

    config = types.GenerateContentConfig(
        temperature=0.15,
        max_output_tokens=max_tokens,
        # Rigorous JSON schema: the model MUST answer in this exact shape.
        response_mime_type="application/json",
        response_schema=schema,
        # Free-tier flash otherwise spends its budget reasoning silently.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    # Encode off the event loop: even a large upload never blocks other
    # requests while the payload is being optimised.
    part = await asyncio.to_thread(_image_part, image, raw_bytes, raw_mime)
    contents = [prompt, part]

    last_quota: Exception | None = None
    for position, key in enumerate(chain):
        if not _key_available(position):
            continue  # memoised exhausted key — no wasted request
        client = genai.Client(
            api_key=key,
            # `attempts` maps to the retry count → 0: ONE HTTP attempt;
            # the SDK default (backoff retries on 429/5XX) is switched off.
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=0)
            ),
        )
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.vision_model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=settings.VISION_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — classified, never looped
            if _failure_kind(exc) == "quota":
                _mark_key_exhausted(position, exc)
                last_quota = exc
                continue  # → next key in the chain (the failover)
            raise  # timeout/network/model error: one honest failure
        return _parse_vision_response(response)

    # Every key either answered 429 (last_quota) or is memoised-blocked.
    if last_quota is not None:
        raise last_quota
    raise RuntimeError("gemini_quota_exhausted_all_keys")


def _clamp01(value: Any) -> float:
    try:
        return round(min(max(float(value), 0.0), 1.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _str_list(payload: dict[str, Any], key: str, *, lo: int = 1, hi: int = 8) -> list[str]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:hi]:
        text = item.strip() if isinstance(item, str) else ""
        if not text and isinstance(item, dict):
            text = str(item.get("text") or item.get("action") or "").strip()
        if text:
            out.append(text[:300])
    if len(out) < lo:
        return []
    return out


# ======================================================================
# Payload coercion — defensive clamps on top of the schema
# ======================================================================
def _coerce_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
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
        out.append({"id": cid, "label": label, "confidence": _clamp01(row.get("confidence", 0.5))})
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
                        {"name_ar": alt, "confidence": _clamp01(row.get("confidence", 0.3))}
                    )

    return {
        "name_ar": name_ar,
        "name_en": name_en,
        "confidence": _clamp01(disease.get("confidence", 0.5)),
        "summary_ar": str(payload.get("summary") or "").strip()[:500],
        "symptoms": _str_list(payload, "symptoms", lo=1, hi=6),
        "immediate_actions": _str_list(payload, "immediate_actions", lo=1, hi=6),
        "cultural_treatments": _str_list(payload, "cultural_treatments", lo=1, hi=6),
        "chemical_treatments": chemicals,
        "monitoring": _str_list(payload, "monitoring", lo=1, hi=4),
        "prevention": _str_list(payload, "prevention", lo=1, hi=4),
        "alternatives": alternatives,
    }


def _coerce_full(payload: dict[str, Any]) -> dict[str, Any]:
    """Combine the single-call payload: crop candidates + disease analysis."""
    analysis = _coerce_disease(payload)
    analysis["candidates"] = _coerce_candidates(payload)
    return analysis


# ======================================================================
# Public API — live-only analysis
# ======================================================================
async def analyze_full(
    image: Image.Image,
    *,
    byte_size: int,
    raw_bytes: bytes | None = None,
    raw_mime: str | None = None,
) -> dict[str, Any]:
    """ONE live Gemini shot for the whole diagnosis (crop + disease + plan).

    The hot path: a single round trip bounded by ``VISION_TIMEOUT_SECONDS``
    (default 45s) — generous enough that free-tier slowness never looks
    like a failure. When the crop
    confidence clears ``CROP_AUTO_THRESHOLD`` the orchestrator continues
    straight to the verdict with the SAME response; otherwise it asks the
    farmer to pick a crop.

    Raises ``VisionUnavailable`` when Gemini cannot answer — there is no
    mock fallback and no fabricated verdict.

    Returns ``{"candidates": [...], "engine": "gemini",
    "latency_ms": int, ...disease analysis fields...}``.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        analysis = _coerce_full(
            await _gemini_json(
                image,
                _full_prompt(),
                max_tokens=2048,
                schema=_VisionPayload,
                raw_bytes=raw_bytes,
                raw_mime=raw_mime,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — honest failure, no fabrication
        logger.warning("vision full stage unavailable (%s) — raising VisionUnavailable", exc)
        raise VisionUnavailable(
            f"gemini full stage failed: {exc}", kind=_failure_kind(exc)
        ) from exc
    analysis["engine"] = "gemini"
    analysis["latency_ms"] = int((loop.time() - started) * 1000)
    return analysis


async def analyze_disease(
    image: Image.Image,
    *,
    crop_label: str,
    crop_id: str | None,
    byte_size: int,
    raw_bytes: bytes | None = None,
    raw_mime: str | None = None,
) -> dict[str, Any]:
    """Deep live Arabic pathology analysis after the farmer confirmed the crop.

    Returns a dict with ``name_ar``/``name_en``/``confidence`` plus
    ``symptoms``, ``immediate_actions``, ``cultural_treatments``,
    ``chemical_treatments``, ``monitoring``, ``prevention``,
    ``alternatives`` (all Arabic), ``engine`` and ``latency_ms``.

    Raises ``VisionUnavailable`` when Gemini cannot answer — no mock.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        analysis = _coerce_disease(
            await _gemini_json(
                image,
                _disease_prompt(crop_label, crop_id),
                schema=_DiseasePayload,
                raw_bytes=raw_bytes,
                raw_mime=raw_mime,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — honest failure, no fabrication
        logger.warning("vision disease stage unavailable (%s) — raising VisionUnavailable", exc)
        raise VisionUnavailable(
            f"gemini disease stage failed: {exc}", kind=_failure_kind(exc)
        ) from exc
    analysis["engine"] = "gemini"
    analysis["latency_ms"] = int((loop.time() - started) * 1000)
    return analysis


# ======================================================================
# Treatment plan assembly (Arabic-first)
# ======================================================================
def _kb_ar(plan: dict[str, Any], key: str) -> list[str]:
    rows = plan.get(key)
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            text = str(row.get("ar") or row.get("en") or "").strip()
            if text:
                out.append(text)
    return out


def candidate_objects(rows: list[dict[str, Any]]) -> list[CropCandidate]:
    """Typed candidates for the API schema."""
    out: list[CropCandidate] = []
    for row in rows:
        try:
            out.append(
                CropCandidate(
                    id=str(row["id"]),
                    label=str(row["label"]),
                    confidence=_clamp01(row.get("confidence", 0.0)),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def treatment_plan_from_analysis(
    analysis: dict[str, Any], crop_label: str | None
) -> TreatmentPlan:
    """Map a live Gemini analysis onto the public ``TreatmentPlan``.

    Arabic strings from the vision model become the plan's primary text
    (``en`` mirrors ``ar`` — the product is Arabic-first); sections the
    model omitted are enriched from the bilingual knowledge base, and the
    existing bilingual disclaimer is always attached. The disease name and
    confidence always come from Gemini — the KB never invents a verdict.
    """
    settings = get_settings()
    kb = build_treatment_plan(str(analysis.get("name_en") or ""), crop_label or "")

    summary_ar = (
        str(analysis.get("summary_ar") or "").strip()
        or "؛ ".join(analysis.get("symptoms") or [])
        or str((kb.get("summary") or {}).get("ar") or "تم تشخيص الحالة وبناء خطة معالجة.")
    )
    immediate = analysis.get("immediate_actions") or _kb_ar(kb, "immediate_actions")
    monitoring = analysis.get("monitoring") or _kb_ar(kb, "monitoring")
    prevention = analysis.get("prevention") or _kb_ar(kb, "prevention")
    cultural = analysis.get("cultural_treatments") or []
    chemical = analysis.get("chemical_treatments") or []
    symptoms = analysis.get("symptoms") or [summary_ar]

    options: list[dict[str, Any]] = []
    for text in cultural:
        options.append({"type": "cultural", "action": {"ar": text, "en": text}, "notes": None})
    for row in chemical:
        product = str(row.get("product") or "")
        usage = str(row.get("usage") or "")
        action = f"{product} — {usage}" if usage else product
        if action:
            options.append({"type": "chemical", "action": {"ar": action, "en": action}, "notes": None})
    if not options:
        options = list(kb.get("control_options") or [])

    return TreatmentPlan(
        summary={"ar": summary_ar, "en": summary_ar},
        immediate_actions=[{"ar": x, "en": x} for x in immediate],
        control_options=options,
        monitoring=[{"ar": x, "en": x} for x in monitoring],
        prevention=[{"ar": x, "en": x} for x in prevention],
        symptoms=[{"ar": s, "en": s} for s in symptoms],
        disclaimer=kb.get("disclaimer") or {"en": "", "ar": ""},
        source=f"gemini_vision:{settings.vision_model_name}",
    )
