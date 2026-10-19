"""DiagnosticAgent — visual pathology of a single crop-leaf image.

Uses the project's **one and only** Gemini API key through the shared client
built in :mod:`agents.config`. Every failure mode is classified into an
:class:`~agents.base.AgentError` so the supervisor can decide what to do;
nothing here ever raises a bare exception to the caller.
"""

from __future__ import annotations

import time
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .base import AgentError, AgentResult, as_dict, as_severity, as_str, as_str_list, clamp01, normalise_image_bytes, parse_model_json
from .config import Settings, get_settings

#: Structured-output contract handed to the model. Keeping it inline (rather
#: than a remote schema) makes the shape auditable and stable.
_DIAGNOSTIC_SCHEMA_PROMPT = """\
You are a plant-pathologist AI. Analyse the crop leaf image and return ONLY a
JSON object with EXACTLY this shape (no prose, no markdown fences):

{
  "is_leaf_image": true,
  "crop": "tomato",
  "disease": "Early blight",
  "disease_ar": "اللفحة المبكرة",
  "pathogen": "Alternaria solani",
  "pathology": "short paragraph on how the pathogen acts on the tissue",
  "symptoms": ["concise symptom", "..."],
  "severity": "none|low|moderate|high|critical",
  "severity_pct": 0.0,
  "confidence": 0.0,
  "confidence_basis": "why this confidence and not higher",
  "differential": ["plausible alternative", "..."],
  "image_quality": "good|usable|poor"
}

Rules:
- "confidence" and "severity_pct" are floats in [0,1].
- Be honest: if the image is blurry, over-exposed or not a leaf, lower
  "confidence" and say so in "confidence_basis"; still fill every field.
- "disease" must be "Healthy" when no pathology is present.
- Write "disease_ar" in natural Arabic; every other value in English.
"""

#: Confidence below this is flagged for the supervisor to treat cautiously.
LOW_CONFIDENCE_THRESHOLD = 0.55


class DiagnosticAgent:
    """Runs a single, schema-enforced vision pass over an uploaded leaf image."""

    name = "diagnostic"

    def __init__(self, settings: Settings | None = None, client: genai.Client | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- infrastructure ----------------------------------------------------

    def _get_client(self) -> genai.Client:
        """Build (once) the single-key Gemini client.

        Raises ``AgentError(kind="auth")`` when no real key is configured, so
        an unconfigured deployment fails loudly and *immediately* rather than
        sending an anonymous request.
        """
        if self._client is not None:
            return self._client

        key = self.settings.api_key
        if not key:
            raise AgentError(
                "auth",
                "GEMINI_API_KEY is not configured. Set it in the project-root .env file.",
                retryable=False,
            )

        timeout_ms = int(max(5.0, self.settings.request_timeout_s) * 1000)
        try:
            self._client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise AgentError("auth", "Failed to initialise the Gemini client.", detail=str(exc)[:160], retryable=False) from exc
        return self._client

    @staticmethod
    def _classify_sdk_error(exc: genai_errors.APIError) -> AgentError:
        """Map a Google API error onto our stable failure taxonomy."""
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)

        if code in (401, 403):
            return AgentError("auth", "Gemini rejected the API key (invalid or forbidden).", retryable=False)
        if code == 429:
            return AgentError("quota", "Gemini API quota exceeded. Please retry later.", retryable=True)
        if code in (408, 500, 502, 503, 504):
            return AgentError("busy", "Gemini service is temporarily unavailable.", retryable=True)
        if code == 400:
            return AgentError("input", "The image payload was rejected by the vision model.", retryable=False)
        return AgentError("api", f"Gemini vision call failed ({code}).", detail=message[:160], retryable=True)

    # -- public API --------------------------------------------------------

    def analyze(self, image_bytes: bytes, *, filename: str | None = None, declared_mime: str | None = None) -> AgentResult:
        """Diagnose one leaf image. Never raises — always returns an AgentResult."""
        started = time.perf_counter()

        try:
            payload, mime = normalise_image_bytes(image_bytes, self.settings.max_image_side, declared_mime)
        except AgentError as exc:
            return AgentResult.failure(exc)

        try:
            client = self._get_client()
        except AgentError as exc:
            return AgentResult.failure(exc)

        try:
            response = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=[
                    types.Part.from_bytes(data=payload, mime_type=mime),
                    _DIAGNOSTIC_SCHEMA_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=self.settings.gemini_temperature,
                    max_output_tokens=self.settings.gemini_max_output_tokens,
                    response_mime_type="application/json",
                    top_p=0.95,
                ),
            )
        except genai_errors.APIError as exc:
            return AgentResult.failure(self._classify_sdk_error(exc))
        except Exception as exc:  # network / timeout / SDK-internal
            return AgentResult.failure(
                AgentError("busy", "The vision request timed out or could not reach Gemini.", detail=str(exc)[:160], retryable=True)
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            raw_text = getattr(response, "text", None)
            report = parse_model_json(raw_text)
        except AgentError as exc:
            return AgentResult.failure(exc)

        return AgentResult.success(self._normalise(report, mime=mime, model=self.settings.gemini_model, elapsed_ms=elapsed_ms, filename=filename))

    # -- output shaping ----------------------------------------------------

    def _normalise(self, report: dict[str, Any], *, mime: str, model: str, elapsed_ms: int, filename: str | None) -> dict[str, Any]:
        """Coerce untrusted model output into a stable, typed shape."""
        report = as_dict(report)
        symptoms = as_str_list(report.get("symptoms"))
        differential = as_str_list(report.get("differential"))

        confidence = clamp01(report.get("confidence"), 0.0)
        severity_pct = clamp01(report.get("severity_pct"), 0.0)
        severity = as_severity(report.get("severity"))

        disease = as_str(report.get("disease"), "Unknown")
        crop = as_str(report.get("crop"), "Unknown")
        healthy = disease.lower() in {"healthy", "no disease", "healthy leaf", "normal"}

        if healthy:
            severity = "none"
            severity_pct = 0.0

        quality = as_str(report.get("image_quality"), "usable").lower()
        if quality not in {"good", "usable", "poor"}:
            quality = "usable"

        return {
            "is_leaf_image": bool(report.get("is_leaf_image", True)),
            "healthy": healthy,
            "crop": crop,
            "disease": disease,
            "disease_ar": as_str(report.get("disease_ar"), "غير معروف"),
            "pathogen": as_str(report.get("pathogen"), "Unknown"),
            "pathology": as_str(report.get("pathology"), ""),
            "symptoms": symptoms,
            "severity": severity,
            "severity_pct": severity_pct,
            "confidence": confidence,
            "confidence_basis": as_str(report.get("confidence_basis"), ""),
            "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
            "differential": differential,
            "image_quality": quality,
            "meta": {
                "agent": self.name,
                "model": model,
                "mime": mime,
                "filename": filename,
                "latency_ms": elapsed_ms,
            },
        }


__all__ = ["DiagnosticAgent", "LOW_CONFIDENCE_THRESHOLD"]
