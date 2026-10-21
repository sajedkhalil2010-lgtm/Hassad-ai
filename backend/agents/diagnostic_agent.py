"""DiagnosticAgent — visual pathology of a single crop-leaf image.

Uses the project's **one and only** Groq API key through the shared client
built in :mod:`agents.config`. Every failure mode is classified into an
:class:`~agents.base.AgentError` so the supervisor can decide what to do;
nothing here ever raises a bare exception to the caller.

Vision call shape (Groq OpenAI-compatible chat completions)::

    client.chat.completions.create(
        model=vision_model,
        messages=[{role: user, content: [text, image_url(data:image/jpeg;base64,...)]}],
        response_format={"type": "json_object"},
    )
"""

from __future__ import annotations

import base64
import time
from typing import Any

from groq import APIError as GroqAPIError

from .base import (
    AgentError,
    AgentResult,
    as_dict,
    as_severity,
    as_str,
    as_str_list,
    clamp01,
    classify_http_error,
    normalise_image_bytes,
    parse_model_json,
    run_with_quota_retry,
)
from .config import Settings, get_settings

#: Structured-output contract handed to the model. Keeping it inline (rather
#: than a remote schema) makes the shape auditable and stable. The keys the
#: frontend depends on — disease / confidence / severity / symptoms /
#: recommendations — are all part of this contract.
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
  "recommendations": ["immediate action the farmer should take", "..."],
  "differential": ["plausible alternative", "..."],
  "image_quality": "good|usable|poor"
}

Rules:
- "confidence" and "severity_pct" are floats in [0,1].
- Be honest: if the image is blurry, over-exposed or not a leaf, lower
  "confidence" and say so in "confidence_basis"; still fill every field.
- "disease" must be "Healthy" when no pathology is present.
- "recommendations" must always be a non-empty array of concrete steps.
- Write "disease_ar" in natural Arabic; every other value in English.
"""

#: Confidence below this is flagged for the supervisor to treat cautiously.
LOW_CONFIDENCE_THRESHOLD = 0.55


class DiagnosticAgent:
    """Runs a single, schema-enforced vision pass over an uploaded leaf image."""

    name = "diagnostic"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- infrastructure ----------------------------------------------------

    def _get_client(self) -> Any:
        """Build (once) the single-key Groq client.

        Raises ``AgentError(kind="auth")`` when no real key is configured, so
        an unconfigured deployment fails loudly and *immediately* rather than
        sending an anonymous request.

        ``max_retries=0`` is deliberate: the Groq SDK retries internally
        (default 2), and stacking that under our own :func:`run_with_quota_retry`
        would multiply into 3x3 calls per analysis.
        """
        if self._client is not None:
            return self._client

        key = self.settings.api_key
        if not key:
            raise AgentError(
                "auth",
                "GROQ_API_KEY is not configured. Set it in the project-root .env file.",
                retryable=False,
            )

        try:
            from groq import Groq

            self._client = Groq(
                api_key=key,
                timeout=max(5.0, self.settings.request_timeout_s),
                max_retries=0,
            )
        except AgentError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise AgentError("auth", "Failed to initialise the Groq client.", detail=str(exc)[:160], retryable=False) from exc
        return self._client

    @staticmethod
    def _classify_sdk_error(exc: BaseException) -> AgentError:
        """Delegate to the shared classifier in ``base``.

        ``media=True`` is the only thing that permits a 400 to be blamed on
        the uploaded bytes — and only when the upstream message actually
        names the image. Every other 400 stays retryable, because Groq
        emits 400 for transient upstream faults as well.
        """
        return classify_http_error(
            exc,
            media=True,
            model_not_found=(
                f"Vision model not found on Groq ({type(exc).__name__}). "
                "Update `vision_model` in agents/config.py."
            ),
        )

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

        # Groq takes images as a data URI inside an OpenAI-style message.
        # normalise_image_bytes already re-encodes to JPEG, so the mime here
        # is always image/jpeg.
        image_b64 = base64.b64encode(payload).decode("ascii")
        data_uri = f"data:{mime};base64,{image_b64}"

        def _generate() -> Any:
            return client.chat.completions.create(
                model=self.settings.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _DIAGNOSTIC_SCHEMA_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                temperature=self.settings.temperature,
                max_completion_tokens=self.settings.max_output_tokens,
                response_format={"type": "json_object"},
            )

        try:
            response = run_with_quota_retry(
                _generate,
                attempts=self.settings.quota_retry_attempts,
                base_delay_s=self.settings.quota_retry_base_delay_s,
            )
        except GroqAPIError as exc:
            return AgentResult.failure(self._classify_sdk_error(exc))
        except AgentError as exc:
            return AgentResult.failure(exc)
        except Exception as exc:  # network / timeout / SDK-internal
            return AgentResult.failure(
                AgentError("busy", "The vision request timed out or could not reach Groq.", detail=str(exc)[:160], retryable=True)
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            report = parse_model_json(self._extract_text(response))
        except AgentError as exc:
            return AgentResult.failure(exc)

        return AgentResult.success(self._normalise(report, mime=mime, model=self.settings.vision_model, elapsed_ms=elapsed_ms, filename=filename))

    @staticmethod
    def _extract_text(response: Any) -> str | None:
        """Pull the assistant text out of a chat-completion object.

        Duck-typed on purpose so tests can hand in a plain stub.
        """
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        if message is None:
            return None
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        # Some responses deliver content as a list of typed parts.
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    return text
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"]
        return None

    # -- output shaping ----------------------------------------------------

    def _normalise(self, report: dict[str, Any], *, mime: str, model: str, elapsed_ms: int, filename: str | None) -> dict[str, Any]:
        """Coerce untrusted model output into a stable, typed shape."""
        report = as_dict(report)
        symptoms = as_str_list(report.get("symptoms"))
        differential = as_str_list(report.get("differential"))
        recommendations = as_str_list(report.get("recommendations"))

        confidence = clamp01(report.get("confidence"), 0.0)
        severity_pct = clamp01(report.get("severity_pct"), 0.0)
        severity = as_severity(report.get("severity"))

        disease = as_str(report.get("disease"), "Unknown")
        crop = as_str(report.get("crop"), "Unknown")
        healthy = disease.lower() in {"healthy", "no disease", "healthy leaf", "normal"}

        if healthy:
            severity = "none"
            severity_pct = 0.0
            if not recommendations:
                recommendations = [
                    "No treatment required; continue the current programme.",
                    "Scout weekly - early detection keeps a plot healthy.",
                ]

        quality = as_str(report.get("image_quality"), "usable").lower()
        if quality not in {"good", "usable", "poor"}:
            quality = "usable"

        if not recommendations:
            recommendations = ["Consult the DPA of Tébessa before applying any phytosanitary product."]

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
            "recommendations": recommendations,
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
