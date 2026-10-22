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


#: Groq validates a completion against ``response_format`` itself and rejects
#: the request outright when the model writes JSON it cannot parse. The two
#: messages it actually emits, both under HTTP 400.
_MALFORMED_OUTPUT_MARKERS = (
    "failed to validate json",
    "failed to generate json",
)


def is_malformed_output_error(exc: BaseException) -> bool:
    """Detect Groq's HTTP 400 for a completion whose JSON could not be parsed.

    This is a **model** fault, not a request fault: the payload we sent was
    well-formed, and the same payload succeeds on the next call. It is also
    strongly language-skewed. With ``response_format=json_object`` and an
    Arabic reply the model intermittently writes JSON that fails validation —
    measured at roughly 1 in 6 Arabic chat turns against 0 in 6 English ones
    on the live account — so a farmer asking in Arabic would otherwise hit a
    "please retry" wall far more often than one asking in English.

    Deliberately narrow: it requires the status code AND the exact wording,
    so a genuine 400 (bad request, oversized payload, unsupported model) is
    never mistaken for this and retried.
    """
    status = getattr(exc, "status_code", None)
    if not (isinstance(status, int) and status == 400):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _MALFORMED_OUTPUT_MARKERS)



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


def run_with_output_retry(
    call: Callable[[], T],
    *,
    attempts: int = 2,
    base_delay_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``call``, regenerating once when the model could not keep valid JSON.

    :func:`run_with_quota_retry` covers the transport failing. This covers the
    other transient case: the transport succeeded and the model produced a
    completion that Groq itself refused to validate (see
    :func:`is_malformed_output_error`). Composing the two — this helper is
    meant to wrap a quota-retried call — means one regeneration gets its own
    full rate-limit budget on top.

    A single extra attempt is the right budget rather than three. The fault is
    in the model's answer, not in our request, so the second draw is close to
    independent of the first; beyond that the farmer is simply waiting.
    """
    if attempts < 1:
        attempts = 1

    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            last_attempt = attempt == attempts - 1
            if last_attempt or not is_malformed_output_error(exc):
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
# HTTP error classification — shared by every agent
# ---------------------------------------------------------------------------

#: Substrings that make an HTTP 400 genuinely the caller's fault on a vision
#: upload. Groq emits 400 for far more than a bad image — a malformed
#: ``response_format``, an over-long context, or a transient upstream fault —
#: so the status code on its own can never justify blaming the farmer's photo.
#: Each of these words appears only in a message about the image payload
#: itself; "boom", "invalid json" or an empty message match none of them.
_MEDIA_REJECTION_MARKERS = (
    "image", "media", "base64", "data uri", "data:image",
    "content type", "content-type", "decode", "jpeg", "jpg", "png", "webp",
    "tiff", "bmp", "attachment",
)


def is_media_rejection(message: str) -> bool:
    """True when an upstream 400 explicitly complains about the uploaded bytes."""
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _MEDIA_REJECTION_MARKERS)


def classify_http_error(
    exc: BaseException,
    *,
    media: bool = False,
    model_not_found: str | None = None,
) -> AgentError:
    """Map a Groq HTTP error onto our stable failure taxonomy.

    One function for every agent so the copies cannot drift — they already
    had, and that drift is what hid the bug below in two of three agents.

    Groq exposes the HTTP status as ``status_code`` (not ``.code``), and
    offers finer-grained subclasses; we read both.

    ``media=True`` marks a vision call. Only then — and only when the upstream
    message actually names the image — may a 400 be attributed to the
    uploaded bytes. Every other 400 is an upstream fault and stays retryable:
    a live run once returned a transient 400 for a payload that succeeded
    seconds later, and reporting that as "your photo is broken, do not
    retry" sent the farmer down the wrong road entirely.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    name = type(exc).__name__
    message = str(getattr(exc, "message", "") or exc)

    if code in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return AgentError("auth", "Groq rejected the API key (invalid or forbidden).", retryable=False)

    if code == 429 or name == "RateLimitError":
        return AgentError(
            "quota",
            QUOTA_MESSAGE_AR,
            retryable=True,
            message_en=QUOTA_MESSAGE_EN,
        )

    if code == 404 or name == "NotFoundError":
        # A retired/unavailable model. Retrying cannot fix this, and it is
        # exactly the failure mode that bit us with Gemini's retired model.
        return AgentError(
            "api",
            model_not_found or f"Model not found on Groq ({name}).",
            detail=message[:160],
            retryable=False,
        )

    if code == 400 or name == "BadRequestError":
        if media and is_media_rejection(message):
            return AgentError(
                "input",
                "The image payload was rejected by the vision model.",
                detail=message[:160],
                retryable=False,
            )
        return AgentError(
            "busy",
            "The model rejected the request; this is usually brief — please retry.",
            detail=message[:160],
            retryable=True,
        )

    if name in ("APIConnectionError", "APITimeoutError") or code in (408, 500, 502, 503, 504):
        return AgentError("busy", "Groq service is temporarily unavailable.", detail=message[:160], retryable=True)

    return AgentError("api", f"Groq call failed ({code}).", detail=message[:160], retryable=True)


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
        from PIL import Image, ImageOps, UnidentifiedImageError
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

            # Cameras record orientation in EXIF rather than rotating the
            # pixels, so a portrait photo arrives sideways. Skipping this
            # asked the model to diagnose a leaf lying on its side - the most
            # common phone-upload variant there is. `format` is read above
            # because the transposed copy no longer carries it.
            transposed = ImageOps.exif_transpose(image)
            if transposed is not None:
                image = transposed

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
    "classify_http_error",
    "is_media_rejection",
    "is_quota_error",
    "normalise_image_bytes",
    "parse_model_json",
    "run_with_quota_retry",
    "sniff_image_mime",
]
