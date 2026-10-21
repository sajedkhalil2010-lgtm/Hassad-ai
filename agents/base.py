"""Shared contracts for the Hassad AI agent layer.

Kept deliberately small and dependency-free (no SDK import here) so that the
supervisor can classify failures without importing the Groq client library.
"""

from __future__ import annotations

import io
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------

#: Failure kinds the HTTP layer knows how to translate into status codes.
FAILURE_KINDS = frozenset({"auth", "quota", "busy", "upstream", "input", "api"})


#: Friendly Arabic message shown to the farmer when the API quota is spent.
#: The frontend renders ``error.message`` directly, so this is what the user
#: actually sees; an English twin is kept alongside for logs and debugging.
#: Provider-neutral: naming a vendor here would go stale on the next migration.
QUOTA_MESSAGE_AR = "تم استهلاك حد الطلبات المؤقت، يرجى الانتظار بضع ثوانٍ ثم المحاولة مجدداً"
QUOTA_MESSAGE_EN = "API rate limit reached. Please retry in a few seconds."


class AgentError(Exception):
    """A classified, structured agent failure.

    Never carries the API key. ``kind`` is one of :data:`FAILURE_KINDS` and
    drives the HTTP mapping in ``main.py``.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retryable: bool = False,
        detail: str | None = None,
        message_en: str | None = None,
    ) -> None:
        super().__init__(message)
        if kind not in FAILURE_KINDS:  # defensive: keep the contract tight
            kind = "api"
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.detail = detail
        #: English twin of ``message`` when ``message`` is localized.
        self.message_en = message_en

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AgentError(kind={self.kind!r}, retryable={self.retryable!r}, message={self.message!r})"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "message": self.message, "retryable": self.retryable}
        if self.message_en:
            payload["message_en"] = self.message_en
        if self.detail:
            payload["detail"] = self.detail
        return payload


# ---------------------------------------------------------------------------
# Quota (HTTP 429) handling
# ---------------------------------------------------------------------------


def is_quota_error(exc: BaseException) -> bool:
    """Detect an HTTP 429 / rate-limit without importing the SDK.

    Duck-typed so this module keeps its zero-SDK-import property. Checks every
    status carrier the common SDKs actually use:

    * ``status_code`` - what groq.APIStatusError exposes. The Groq SDK sets
      neither ``.code`` nor ``.status``, so a check aimed only at those would
      silently never match and quietly disable the retry.
    * ``code`` - what google.genai.errors.APIError exposed (kept so this
      helper stays provider-agnostic).
    * ``status`` / message text - RESOURCE_EXHAUSTED-style payloads.
    """
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and value == 429:
            return True
        if isinstance(value, str) and value.strip() == "429":
            return True

    status = str(getattr(exc, "status", "") or "")
    if "RESOURCE_EXHAUSTED" in status.upper() or "RATE_LIMIT" in status.upper():
        return True
    return "RESOURCE_EXHAUSTED" in str(exc).upper()



def run_with_quota_retry(
    call: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``call``, retrying only on rate-limit (429) errors with backoff.

    Delays are exponential from ``base_delay_s`` (2s, 4s, ...) and are kept
    short on purpose: a full analysis already takes tens of seconds, so a
    long backoff would just make the farmer wait for a failure.

    Non-quota errors are re-raised immediately — never retry a bad request
    or an invalid key, since that only burns more quota.

    ``sleep`` is injectable so tests can assert the schedule without waiting.
    """
    if attempts < 1:
        attempts = 1

    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            last_attempt = attempt == attempts - 1
            if last_attempt or not is_quota_error(exc):
                raise
            sleep(base_delay_s * (2 ** attempt))

    raise RuntimeError("unreachable: retry loop exhausted")  # pragma: no cover


# ---------------------------------------------------------------------------
# Structured result envelope
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Uniform wrapper returned by every agent's public entry point."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: AgentError | None = None

    @classmethod
    def success(cls, data: dict[str, Any] | None) -> "AgentResult":
        """Build a success envelope, coercing falsy payloads to a clean dict."""
        return cls(ok=True, data=data if isinstance(data, dict) else {})

    @classmethod
    def failure(cls, error: AgentError) -> "AgentResult":
        return cls(ok=False, error=error)

    def to_dict(self) -> dict[str, Any]:
        if self.ok:
            return {"ok": True, **self.data}
        return {"ok": False, "error": (self.error or AgentError("api", "unknown failure")).to_dict()}


# ---------------------------------------------------------------------------
# Defensive coercion helpers (model output is untrusted)
# ---------------------------------------------------------------------------


def clamp01(value: Any, default: float = 0.0) -> float:
    """Coerce to a float in ``[0, 1]``; anything unusable falls back."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN guard
        return default
    return max(0.0, min(1.0, number))


def as_str(value: Any, default: str = "") -> str:
    """Coerce to a trimmed non-empty string."""
    if isinstance(value, str):
        text = value.strip()
        return text if text else default
    if value is None:
        return default
    return str(value).strip() or default


def as_str_list(value: Any) -> list[str]:
    """Coerce a scalar/list into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = as_str(item)
            if text:
                out.append(text)
        return out
    return [str(value)]


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_SEVERITIES = ("none", "low", "moderate", "high", "critical", "unknown")


def as_severity(value: Any) -> str:
    """Normalise a severity label to a known enum member."""
    text = as_str(value, "unknown").lower()
    for known in _SEVERITIES:
        if known in text:
            return known
    return "unknown"


# ---------------------------------------------------------------------------
# Robust JSON extraction from model text
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_model_json(raw: Any) -> dict[str, Any]:
    """Parse a JSON object out of untrusted model output.

    Handles bare JSON, ```json fences, leading prose and trailing commentary.
    Raises :class:`AgentError` with ``kind="api"`` when nothing parseable is
    found — the caller decides whether that is fatal.
    """
    if isinstance(raw, dict):
        return raw
    if raw is None:
        raise AgentError("api", "Empty response from the vision model.", retryable=True)

    text = raw.strip() if isinstance(raw, str) else str(raw).strip()
    if not text:
        raise AgentError("api", "Empty response from the vision model.", retryable=True)

    # 1. The whole payload parses. If it is valid JSON but NOT an object, that
    #    is a schema violation we must reject rather than silently paper over.
    try:
        whole = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass  # fall through to the extraction strategies below
    else:
        if isinstance(whole, dict):
            return whole
        raise AgentError("api", "Model returned JSON that is not an object.", detail=text[:200], retryable=False)

    # 2. The model wrapped an object in prose / fences / chatter: extract it.
    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise AgentError("api", "Model returned a non-JSON payload.", detail=text[:200], retryable=False)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

_MIME_BY_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # refined below
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

SUPPORTED_MIME_TYPES = ("image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff")


def sniff_image_mime(data: bytes, declared: str | None = None) -> str | None:
    """Detect an image MIME type from magic bytes, with a WebP refinement."""
    if not data:
        return None
    for magic, mime in _MIME_BY_MAGIC:
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    if declared:
        normalised = declared.split(";")[0].strip().lower()
        if normalised in SUPPORTED_MIME_TYPES:
            return normalised
    return None


def normalise_image_bytes(data: bytes, max_side: int = 1600, declared_mime: str | None = None) -> tuple[bytes, str]:
    """Validate + downscale an image for the vision call.

    Returns ``(bytes, mime)``. Raises :class:`AgentError` with ``kind="input"``
    for anything that is not a decodable image. Downscaling keeps payloads
    well under the API limit and cuts latency.
    """
    if not data:
        raise AgentError("input", "The uploaded file is empty.", retryable=False)

    mime = sniff_image_mime(data, declared_mime)
    if mime is None:
        raise AgentError("input", "Unsupported file type. Please upload a JPEG, PNG or WebP image.", retryable=False)

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        return data, mime

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or "").upper()
            if fmt == "JPEG":
                mime = "image/jpeg"
            elif fmt == "PNG":
                mime = "image/png"
            elif fmt == "WEBP":
                mime = "image/webp"

            rgba_like = mime in {"image/png", "image/webp", "image/tiff"}
            if rgba_like and image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            longest = max(image.size)
            if longest > max_side:
                ratio = max_side / float(longest)
                new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
                image = image.resize(new_size, Image.LANCZOS)

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except AgentError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AgentError("input", "The uploaded file could not be read as an image.", detail=str(exc)[:160], retryable=False)


__all__ = [
    "AgentError",
    "AgentResult",
    "FAILURE_KINDS",
    "QUOTA_MESSAGE_AR",
    "QUOTA_MESSAGE_EN",
    "SUPPORTED_MIME_TYPES",
    "as_dict",
    "as_severity",
    "as_str",
    "as_str_list",
    "clamp01",
    "is_quota_error",
    "normalise_image_bytes",
    "parse_model_json",
    "run_with_quota_retry",
    "sniff_image_mime",
]
