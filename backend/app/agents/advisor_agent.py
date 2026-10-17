"""AdvisorAgent — localized treatment plans and conversational advice.

Takes a DiagnosticAgent verdict and formulates the expert advisory layer:

* :meth:`AdvisorAgent.build_plan` — a tailored, **localized** (Algeria /
  MENA agronomy) treatment plan mapped onto the public ``TreatmentPlan``:
  immediate actions, cultural + organic/chemical control options,
  monitoring and prevention. Sections the model omitted are enriched from
  the bilingual knowledge base (``ai_vision.build_treatment_plan``); the
  disease name and confidence always come from the diagnosis — the KB
  never invents a verdict.
* :meth:`AdvisorAgent.stream_advice` / :meth:`answer_once` — the chat
  assistant: RAG-grounded, streamed through the CognitionEngine
  gateway (multi-key rotation on 429, zero SDK retries, hard total
  deadline). The chatbot **must always answer**: when no key can start
  a stream, the agent degrades to a retrieval-only answer and reports
  ``mode="retrieval_fallback"`` — it never dead-ends.

v3: the agent holds no SDK code — all reasoning goes through
:mod:`app.agents.cognition`; plan synthesis is grounded (diagnosis
fields first, knowledge-base enrichment second, bilingual disclaimer
always) and is validated non-empty before it ships.

English-first formatting (bilingual ``LocalizedText`` rows preserved);
chat answers honour the caller's ``lang``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.agents.base import AgentFailure
from app.agents.cognition import CognitionEngine
from app.agents.key_manager import KeyPoolManager
from app.agents import knowledge
from app.core.config import get_settings
from app.models.chatbot import KnowledgeBase
from app.schemas.diagnosis import TreatmentPlan

logger = logging.getLogger("hassad.agents.advisor")


class AdvisorAgent:
    """Advisory stage: treatment plans (from diagnosis) + chat answers."""

    name = "advisor"

    # ==================================================================
    # Treatment plans (Crop Doctor pipeline)
    # ==================================================================
    def build_plan(self, analysis: dict[str, Any], crop_label: str | None) -> TreatmentPlan:
        """Map a live diagnostic verdict onto the public ``TreatmentPlan``.

        English strings from the diagnostic agent become the plan's
        primary text (mirrored into the bilingual contract rows); sections
        the model omitted are enriched from the local knowledge base and
        the standard bilingual disclaimer is always attached.
        """
        from app.services.ai_vision import build_treatment_plan

        settings = get_settings()
        kb = build_treatment_plan(str(analysis.get("name_en") or ""), crop_label or "")

        summary = (
            str(analysis.get("summary") or "").strip()
            or "; ".join(analysis.get("symptoms") or [])
            or str((kb.get("summary") or {}).get("en") or "Condition diagnosed; plan below.")
        )
        immediate = analysis.get("immediate_actions") or _kb_text(kb, "immediate_actions")
        monitoring = analysis.get("monitoring") or _kb_text(kb, "monitoring")
        prevention = analysis.get("prevention") or _kb_text(kb, "prevention")
        cultural = analysis.get("cultural_treatments") or []
        chemical = analysis.get("chemical_treatments") or []
        symptoms = analysis.get("symptoms") or [summary]

        options: list[dict[str, Any]] = []
        for text in cultural:
            options.append({"type": "cultural", "action": {"ar": text, "en": text}, "notes": None})
        for row in chemical:
            product = str(row.get("product") or "")
            usage = str(row.get("usage") or "")
            action = f"{product} — {usage}" if usage else product
            if action:
                options.append(
                    {"type": "chemical", "action": {"ar": action, "en": action}, "notes": None}
                )
        if not options:
            options = list(kb.get("control_options") or [])

        return TreatmentPlan(
            summary={"ar": summary, "en": summary},
            immediate_actions=[{"ar": x, "en": x} for x in immediate],
            control_options=options,
            monitoring=[{"ar": x, "en": x} for x in monitoring],
            prevention=[{"ar": x, "en": x} for x in prevention],
            symptoms=[{"ar": s, "en": s} for s in symptoms],
            disclaimer=kb.get("disclaimer") or {"en": "", "ar": ""},
            source=f"agents:{settings.vision_model_name}",
        )

    # ==================================================================
    # Conversational advice (chatbot) — streamed, always answers
    # ==================================================================
    async def stream_advice(
        self,
        *,
        question: str,
        docs: Sequence[KnowledgeBase],
        crop_context: dict[str, Any] | None,
        history: Sequence[dict[str, str]],
        lang: str,
        temperature: float | None = None,
        mode_out: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Yield the chat answer token-by-token (free tier, hard deadline).

        Never raises for LLM problems: when no key can start a stream
        (missing config, quota, SDK failure, timeout, empty answer) this
        degrades to a retrieval-only answer and records
        ``mode_out["mode"] = "retrieval_fallback"`` so callers report the
        real mode — the chatbot must always answer.
        """
        settings = get_settings()
        prompt = knowledge.build_prompt(
            question=question,
            docs=docs,
            crop_context=crop_context,
            history=history,
            lang=lang,
        )

        def _degrade(reason: str) -> None:
            if mode_out is not None:
                mode_out["mode"] = "retrieval_fallback"
                mode_out["reason"] = reason

        if not KeyPoolManager.any_configured():
            logger.warning("no Gemini key configured — retrieval-only fallback answers")
            _degrade("not_configured")
            async for piece in _stream_fallback(docs, lang, reason="not_configured"):
                yield piece
            return

        try:
            config = CognitionEngine.config(
                temperature=(
                    temperature if temperature is not None else settings.GEMINI_TEMPERATURE
                ),
                max_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
                top_p=0.95,
            )
            produced = 0
            async for piece in CognitionEngine.stream_text(
                model=settings.GEMINI_MODEL_NAME,
                contents=prompt,
                config=config,
                budget=float(settings.GEMINI_TIMEOUT_SECONDS),
                agent=self.name,
            ):
                produced += len(piece)
                yield piece
            if produced == 0:
                logger.warning("model returned an empty stream — retrieval fallback")
                _degrade("empty")
                async for fb in _stream_fallback(docs, lang, reason="empty"):
                    yield fb
        except AgentFailure as exc:
            reason = "timeout" if "timeout" in str(exc).lower() else "failed"
            logger.warning("advisor stream unavailable (%s) — retrieval fallback", reason)
            _degrade(reason)
            async for fb in _stream_fallback(docs, lang, reason=reason):
                yield fb

    async def answer_once(
        self,
        *,
        question: str,
        docs: Sequence[KnowledgeBase],
        crop_context: dict[str, Any] | None,
        history: Sequence[dict[str, str]],
        lang: str,
        temperature: float | None = None,
        mode_out: dict[str, str] | None = None,
    ) -> str:
        """Non-streaming helper (used by ``/chatbot/ask`` and by tests)."""
        parts: list[str] = []
        async for piece in self.stream_advice(
            question=question,
            docs=docs,
            crop_context=crop_context,
            history=history,
            lang=lang,
            temperature=temperature,
            mode_out=mode_out,
        ):
            parts.append(piece)
        return "".join(parts)


def _kb_text(plan: dict[str, Any], key: str) -> list[str]:
    rows = plan.get(key)
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            text = str(row.get("en") or row.get("ar") or "").strip()
            if text:
                out.append(text)
    return out


async def _stream_fallback(
    docs: Sequence[KnowledgeBase], lang: str, *, reason: str = "not_configured"
) -> AsyncIterator[str]:
    """Retrieval-only answer used whenever the model cannot answer right now."""
    if lang == "ar":
        intros = {
            "not_configured": "لم يتم ضبط مفتاح Gemini بعد، وهذه أقرب المعلومات من قاعدة المعرفة:",
            "timeout": "يستغرق الرد التفاعل وقتاً أطول من المتوقع الآن، وهذه أقرب المعلومات من قاعدة المعرفة:",
            "failed": "تعذّر الوصول إلى النموذج التفاعلي الآن، وهذه أقرب المعلومات من قاعدة المعرفة:",
            "empty": "لم يُرجع النموذج إجابة هذه المرة، وهذه أقرب المعلومات من قاعدة المعرفة:",
        }
        outros = {
            "not_configured": "\n\n(أضف GEMINI_API_KEY إلى ملف .env لتفعيل الإجابات التفاعلية الكاملة.)",
            "timeout": "\n\n(أعد المحاولة بعد قليل للحصول على إجابة تفاعلية كاملة.)",
            "failed": "\n\n(أعد المحاولة بعد قليل للحصول على إجابة تفاعلية كاملة.)",
            "empty": "\n\n(أعد صياغة السؤال أو أعد المحاولة.)",
        }
    else:
        intros = {
            "not_configured": "Gemini is not configured yet — here is the closest knowledge-base guidance:",
            "timeout": "The AI answer is taking longer than expected right now — here is the closest knowledge-base guidance:",
            "failed": "The AI model is unreachable right now — here is the closest knowledge-base guidance:",
            "empty": "The model returned no answer this time — here is the closest knowledge-base guidance:",
        }
        outros = {
            "not_configured": "\n\n(Add GEMINI_API_KEY to .env to enable the full conversational answers.)",
            "timeout": "\n\n(Retry shortly for a full conversational answer.)",
            "failed": "\n\n(Retry shortly for a full conversational answer.)",
            "empty": "\n\n(Retry with a rephrased question.)",
        }
    intro = intros.get(reason, intros["failed"])
    outro = outros.get(reason, outros["failed"])
    yield intro + "\n\n"
    for card in knowledge.citation_cards(docs):
        yield f"[{card['index']}] {card['title']}: {card['snippet']}\n"
    yield outro
