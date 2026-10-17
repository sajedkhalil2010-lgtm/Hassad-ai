"""Hassad AI v2/v3 — shared multi-agent contracts.

Small, dependency-free primitives every agent builds on:

* :class:`AgentFailure` — the *only* way an agent reports that the
  underlying model could not deliver. ``kind`` is ``"quota"`` (the free
  tier's daily allowance ran out — retry after Google's reset window) or
  ``"busy"`` (congestion/timeout — retry in moments). No agent ever
  fabricates a verdict; failures propagate so the router can answer with
  an honest, structured HTTP 429/503.
* :class:`AgentContext` — one bag of run inputs (image bytes, crop
  hints, language preference, deadlines) threaded through the
  supervisor → agents sequence.
* :class:`RunTrace` — the shared **blackboard**: every agent appends its
  reasoning steps (act → outcome → milliseconds) as it decides what to
  do next. The supervisor ships it in ``model_meta.agents.trace`` as
  auditable proof of autonomous coordination.
* Defensive payload coercers + the canonical crop vocabulary, shared by
  the diagnostic pipeline.

All formatting here is **English-first** (keys, statuses, messages), with
bilingual schema fields preserved where the wire contract has them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

#: (id, English name, Arabic name) — canonical crop vocabulary; the
#: diagnostic agent is instructed to choose from this list so selection
#: ids stay stable for the (untouched) frontend.
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

#: mime lookup for the validated upload formats.
_RAW_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def raw_mime_for(image_format: str) -> str:
    """MIME type for a validated upload format (``JPEG``/``PNG``/``WEBP``)."""
    return _RAW_MIME.get(str(image_format or "").upper(), "image/jpeg")


class AgentFailure(RuntimeError):
    """An agent could not deliver a live result — never swallowed.

    Raised by every agent stage; the supervisor and routers convert it
    into structured, user-friendly HTTP errors instead of fabricating
    output. ``kind``: ``"quota"`` | ``"busy"``.
    """

    def __init__(self, message: str, *, kind: str = "busy", agent: str = "") -> None:
        super().__init__(message)
        self.kind = kind if kind in ("quota", "busy") else "busy"
        self.agent = agent


def failure_kind(exc: Exception) -> str:
    """Classify an upstream SDK error payload into quota | busy."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text or "quota" in text.lower():
        return "quota"
    return "busy"


class RunTrace:
    """Shared blackboard of reasoning steps for one pipeline run.

    Agents append ``{agent, step, action, outcome, ms}`` as they decide
    their next move — the supervisor serialises it into
    ``model_meta.agents.trace`` (additive key: the untouched frontend
    never reads it, presentations and audits do).
    """

    __slots__ = ("_rows",)

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def record(
        self,
        agent: str,
        step: str,
        action: str,
        outcome: str,
        *,
        ms: int | None = None,
        **extra: Any,
    ) -> None:
        row: dict[str, Any] = {
            "agent": agent,
            "step": step,
            "action": action,
            "outcome": outcome,
        }
        if ms is not None:
            row["ms"] = int(ms)
        if extra:
            row.update(extra)
        self._rows.append(row)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


@dataclass
class AgentContext:
    """Run inputs threaded through the agent sequence.

    ``lang`` follows the English-first standard: ``"en"`` unless the
    client explicitly asks for Arabic (the chatbot sends ``lang=ar``).
    ``trace`` is the shared blackboard every agent records into.
    """

    image: Image.Image | None = None
    raw_bytes: bytes | None = None
    raw_mime: str | None = None
    byte_size: int = 0
    selected_crop: str | None = None
    crop_id: str | None = None
    crop_label: str | None = None
    notes: str | None = None
    farm_location: str | None = None
    lang: str = "en"
    #: Wall-clock budget (seconds) the supervisor grants the diagnostic
    #: agent for ALL of its reasoning passes combined (hypothesis,
    #: self-verification, bounded refine). ``None`` → engine default.
    budget_s: float | None = None
    trace: RunTrace = field(default_factory=RunTrace)
    meta: dict[str, Any] = field(default_factory=dict)


# ======================================================================
# Defensive payload coercers (on top of the enforced JSON schema)
# ======================================================================
def clamp01(value: Any) -> float:
    try:
        return round(min(max(float(value), 0.0), 1.0), 4)
    except (TypeError, ValueError):
        return 0.0


def str_list(payload: dict[str, Any], key: str, *, lo: int = 1, hi: int = 8) -> list[str]:
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


def parse_json_response(response: Any) -> dict[str, Any]:
    """Schema-validated payload from the SDK, or a manual JSON fallback."""
    # Preferred: the SDK already validated the body against the schema.
    parsed = getattr(response, "parsed", None)
    if hasattr(parsed, "model_dump"):  # pydantic model (BaseModel subclass)
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed

    # Safety net: parse the raw JSON text ourselves.
    text = (response.text or "").strip()
    if not text:
        raise ValueError("empty model response")
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"non-JSON model response: {text[:80]!r}")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model JSON is not an object")
    return payload
