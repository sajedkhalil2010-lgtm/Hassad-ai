"""ChatAgent — interactive agronomist conversation with a farmer.

Lets a farmer keep talking after a diagnosis: ask about dosage, timing,
alternatives, or a different crop entirely. One text turn per request, with
the client sending back whatever history it holds.

Like the other agents it never raises — every failure becomes an
:class:`~agents.base.AgentError` so the HTTP layer can map it onto a status
code and the farmer always sees a readable message.

Call shape (Groq OpenAI-compatible chat completions)::

    client.chat.completions.create(
        model=chat_model,
        messages=[system, (optional diagnosis context), *history, user],
        response_format={"type": "json_object"},
    )

Deliberately **no knowledge-base fallback**. The advisor can fall back to an
embedded Tébessa KB because its questions are a closed set. Open-ended chat
cannot: inventing an answer to "how much copper do I spray on my olives?"
would be worse than admitting the outage, because a wrong dose destroys a
crop. So a failed chat turn returns an honest, retryable error instead.

The model id defaults to the fast ``openai/gpt-oss-20b``. The originally
requested ``llama-3.3-70b-versatile`` returns HTTP 404 on this account (it
was retired, despite being listed as a Production model in the docs), which
is why :mod:`agents.config` documents the substitution.
"""

from __future__ import annotations

import time
from typing import Any

from groq import APIError as GroqAPIError

from .base import (
    AgentError,
    AgentResult,
    as_dict,
    as_str,
    as_str_list,
    clamp01,
    classify_http_error,
    parse_model_json,
    run_with_output_retry,
    run_with_quota_retry,
)
from .config import Settings, get_settings
from .language import DEFAULT_LANGUAGE, reply_instruction, resolve_language

#: System prompt. Written to produce advice a farmer in the Algerian steppe
#: can act on today, and to keep the model inside its competence.
_SYSTEM_PROMPT = """\
You are Hassad AI's agronomist: an expert in Algerian agriculture advising
smallholder farmers in Tébessa province and similar semi-arid steppe regions.

Local knowledge to draw on:
- Main crops: durum wheat, barley, date palm, olive, fig, potato, tomato,
  pepper, onion, citrus, alfalfa, and orchard fruit.
- Steppe constraints: erratic rainfall, drought, soil salinity, scarce
  irrigation water, khamsin wind, and sudden heat spikes.
- Common local pressures: wheat rusts and septoria, powdery mildew, Tuta
  absoluta on tomato, olive peacock spot, bayoud disease on date palm, desert
  locust, aphids and whitefly.

Rules:
1. The reply language is DECIDED BY THE SERVER and handed to you as an
   explicit directive at the end of this prompt. Follow it exactly. Failing
   that directive, reply in the SAME language the farmer writes in (Arabic,
   French or English). Arabic must be natural and farmer-friendly, not
   bookish.
2. Be concrete: what to do, when, and at what rate or dose. Avoid theory.
3. Prefer cultural and biological control. When a phytosanitary product is
   genuinely needed, name the ACTIVE INGREDIENT and its rate — not a brand,
   since brands differ by market.
4. Never invent a registration. Always tell the farmer to confirm the label,
   the approved use and the pre-harvest interval with the local DPA
   (Direction de la Protection des Végétaux) before spraying.
5. Never recommend anything not registered in Algeria, and never give
   medical or veterinary advice — refer a sick person or animal to the
   right professional.
6. Do not pretend certainty from text alone. If the farmer describes leaf
   symptoms, suggest running the image diagnosis for a reliable result.
7. Keep the answer short and scannable: a few sentences, or a short list. It
   is being read on a phone in a field.
8. If unsure, say so plainly and recommend the DPA. An honest "I am not
   certain" protects the harvest better than a confident wrong answer.

Return ONLY a JSON object, with no prose and no markdown fences:
{"reply": "<the answer, in the farmer's language>", "suggestions": ["<follow-up question 1>", "<follow-up question 2>", "<follow-up question 3>"]}

"suggestions" must be 2 to 4 short follow-up questions the farmer can tap
next, each answerable from your agronomy knowledge.
"""

#: How many follow-up suggestions we surface, after whatever the model sent.
MAX_SUGGESTIONS = 4


class ChatAgent:
    """One conversational turn, grounded as an Algerian agronomist."""

    name = "chat"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- infrastructure ----------------------------------------------------

    def _get_client(self) -> Any:
        """Build (once) the single-key Groq client.

        ``max_retries=0`` keeps Groq's internal retries off so the only backoff
        in play is our own :func:`run_with_quota_retry`; stacking the two would
        multiply into 3x3 calls for one farmer question.
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

        This closes the defect logged in entry 26: a transient Groq 400 used
        to be reported to the farmer as "The chat request was rejected by the
        model" with ``retryable=False``, even though the identical payload
        succeeded moments later. The chat route carries no image and no
        caller-controlled media, so a 400 is never the farmer's fault — it
        now stays retryable.
        """
        return classify_http_error(
            exc,
            model_not_found=(
                f"Chat model not found on Groq ({type(exc).__name__}). "
                "Set CHAT_MODEL in .env to a model your account serves."
            ),
        )

    # -- input hygiene (client-supplied text is untrusted) ------------------

    def _clean_history(self, history: Any) -> list[dict[str, str]]:
        """Coerce client-supplied history into a bounded, role-checked list.

        The frontend is not a trust boundary: a caller could post arbitrary
        roles or megabytes of text, and whatever reaches the model is part of
        a prompt. Roles are whitelisted, content is truncated, and the oldest
        turns are dropped beyond ``max_chat_history``.
        """
        if not isinstance(history, (list, tuple)):
            return []

        cleaned: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role not in ("user", "assistant"):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            cleaned.append({"role": role, "content": content[: self.settings.max_chat_chars]})

        limit = max(0, self.settings.max_chat_history)
        return cleaned[-limit:] if limit else []

    def _diagnosis_context(self, diagnosis: Any) -> str:
        """Render a prior diagnosis as a compact, bounded context block.

        Only whitelisted scalar fields are copied — never the raw payload —
        so a caller cannot smuggle instructions in through a "diagnosis".
        """
        d = as_dict(diagnosis)
        if not d:
            return ""

        lines: list[str] = []
        crop = as_str(d.get("crop"), "")
        disease = as_str(d.get("disease"), "")
        if crop:
            lines.append(f"Crop: {crop}")
        if disease:
            lines.append(f"Diagnosed disease: {disease}")
        if lines:
            severity = as_str(d.get("severity"), "")
            if severity:
                lines.append(f"Severity: {severity} ({clamp01(d.get('confidence'), 0.0):.0%} confidence)")
        symptoms = as_str_list(d.get("symptoms"))[:5]
        if symptoms:
            lines.append(f"Symptoms observed: {', '.join(symptoms)}")
        recommendations = as_str_list(d.get("recommendations"))[:5]
        if recommendations:
            lines.append(f"Actions already recommended: {' | '.join(recommendations)}")

        if not lines:
            return ""
        return "Context from the farmer's most recent image diagnosis (use only if relevant to the question):\n" + "\n".join(lines)

    def _build_messages(
        self,
        message: str,
        history: list[dict[str, str]],
        diagnosis: Any,
        language: str = DEFAULT_LANGUAGE,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]

        context = self._diagnosis_context(diagnosis)
        if context:
            messages.append({"role": "system", "content": context})

        # Sent last so it outranks both the static prompt and any older turn
        # that was produced in the other language.
        messages.append({"role": "system", "content": reply_instruction(language)})

        messages.extend(history)
        messages.append({"role": "user", "content": message})
        return messages

    # -- public API --------------------------------------------------------

    def ask(
        self,
        message: str,
        *,
        history: Any = None,
        diagnosis: dict[str, Any] | None = None,
        language: str | None = None,
    ) -> AgentResult:
        """Answer one farmer question. Never raises — always returns a result.

        ``language`` overrides detection, which is the right thing for an app
        whose locale is set to Arabic while the farmer types a Latin-script
        cultivar name. Without an override the message itself is classified.
        """
        started = time.perf_counter()

        text = str(message or "").strip()
        if not text:
            return AgentResult.failure(AgentError("input", "Please type a question first.", retryable=False))
        if len(text) > self.settings.max_chat_chars:
            return AgentResult.failure(
                AgentError(
                    "input",
                    f"Your message is too long ({len(text)} characters). The limit is {self.settings.max_chat_chars}.",
                    retryable=False,
                )
            )

        lang = resolve_language(language, text)

        try:
            client = self._get_client()
        except AgentError as exc:
            return AgentResult.failure(exc)

        turns = self._clean_history(history)
        messages = self._build_messages(text, turns, diagnosis, lang)

        def _generate() -> Any:
            return client.chat.completions.create(
                model=self.settings.chat_model,
                messages=messages,
                temperature=self.settings.temperature,
                max_completion_tokens=min(1024, self.settings.max_output_tokens),
                response_format={"type": "json_object"},
            )

        try:
            response = run_with_output_retry(
                lambda: run_with_quota_retry(
                    _generate,
                    attempts=self.settings.quota_retry_attempts,
                    base_delay_s=self.settings.quota_retry_base_delay_s,
                ),
                attempts=self.settings.output_retry_attempts,
                base_delay_s=self.settings.output_retry_base_delay_s,
            )
        except GroqAPIError as exc:
            return AgentResult.failure(self._classify_sdk_error(exc))
        except AgentError as exc:
            return AgentResult.failure(exc)
        except Exception as exc:  # network / timeout / SDK-internal
            return AgentResult.failure(
                AgentError("busy", "The chat request timed out or could not reach Groq.", detail=str(exc)[:160], retryable=True)
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            report = parse_model_json(self._extract_text(response))
        except AgentError as exc:
            return AgentResult.failure(exc)

        # `_normalise` deliberately raises when the model omits the reply we
        # depend on, so it MUST be guarded here — otherwise an upstream oddity
        # escapes as a bare exception instead of a clean, structured failure.
        try:
            data = self._normalise(
                report, model=self.settings.chat_model, elapsed_ms=elapsed_ms, history_turns=len(turns), language=lang
            )
        except AgentError as exc:
            return AgentResult.failure(exc)

        return AgentResult.success(data)

    @staticmethod
    def _extract_text(response: Any) -> str | None:
        """Pull the assistant text out of a chat-completion object.

        Duck-typed so tests can hand in a plain stub.
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
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    return text
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"]
        return None

    # -- output shaping ----------------------------------------------------

    def _normalise(
        self,
        report: dict[str, Any],
        *,
        model: str,
        elapsed_ms: int,
        history_turns: int,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, Any]:
        """Coerce untrusted model output into the shape the chat UI needs."""
        report = as_dict(report)

        reply = as_str(report.get("reply"), "").strip()
        suggestions = [s.strip() for s in as_str_list(report.get("suggestions"))[:MAX_SUGGESTIONS] if s.strip()]

        if not reply:
            # JSON parsed but the model omitted the field we depend on. This is
            # a retryable upstream oddity, not the farmer's fault.
            raise AgentError("api", "The chat model returned an empty reply. Please try again.", retryable=True)

        return {
            "reply": reply,
            "suggestions": suggestions,
            "meta": {
                "agent": self.name,
                "model": model,
                "source": "groq",
                "latency_ms": elapsed_ms,
                "history_turns": history_turns,
                "language": language,
            },
        }


__all__ = ["ChatAgent", "MAX_SUGGESTIONS"]
