"""Agronomy RAG service — Google Gemini **free tier** + local knowledge base.

Pipeline
--------
1. **Context enrichment** — the caller's farm/plot/last-diagnosis context is
   serialised and pre-filled into the prompt.
2. **Retrieval** — a lightweight, language-agnostic (Arabic + English) lexical
   scorer ranks ``KnowledgeBase`` excerpts.  No vector DB is required, which
   keeps the whole stack free-tier and offline-friendly; swap in pgvector later
   by replacing :func:`retrieve`.
3. **Generation** — Gemini streams the answer with inline ``[n]`` markers.
4. **Citations** — markers are mapped back to the retrieved documents and
   returned as citation cards.

Security
--------
``GEMINI_API_KEY`` is read from the environment/.env at *call time* via
pydantic-settings.  It is never hard-coded, never logged and never echoed back.
When the key is missing the chatbot degrades to a retrieval-only answer
(``mode="retrieval_fallback"``) instead of failing — and logs a warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.chatbot import KnowledgeBase

logger = logging.getLogger("hassad.rag")

# ======================================================================
# Arabic-aware text normalisation & scoring
# ======================================================================
_TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_ALEF_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
# \w covers Unicode letters (incl. Arabic) — the old [\u0600-\u06FF] range
# wrongly admitted Arabic punctuation (؟ ، ؛), which glued itself to the last
# word of every question and silently broke matching.
_TOKEN_RE = re.compile(r"\w{3,}", re.UNICODE)
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def normalize_text(text: str) -> str:
    """Lowercase, strip Arabic diacritics and unify alef/ya/ta-marbuta forms."""
    lowered = (text or "").lower().strip()
    no_diacritics = _TASHKEEL_RE.sub("", lowered)
    return no_diacritics.translate(_ALEF_MAP)


# Agronomy synonyms the knowledge base and farmers use interchangeably
# (the KB writes ازوت for nitrogen; questions almost always say نيتروجين).
# Built through normalize_text so keys/values match normalised tokens exactly.
_SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    normalize_text(k): tuple(normalize_text(a) for a in aliases)
    for k, aliases in {
        "نيتروجين": ("ازوت", "ازوتيه"),
        "ازوت": ("نيتروجين", "ازوتيه"),
        "ازوتيه": ("نيتروجين", "ازوت"),
        "بليط": ("لفحة",),
        "لفحة": ("بليط",),
        "ذبابة": ("آفات", "حشرات"),
        "أري": ("الري",),
        "محصولي": ("محاصيل",),
    }.items()
}


_AR_PREFIX_RE = re.compile(r"^(وال|بال|كال|فال|ال|لل)")


def tokenize(text: str) -> set[str]:
    """Word tokens (>= 3 chars) of the normalised text.

    Applies a light Arabic defixing step: the fused definite article
    (ال/وال/بال/كال/فال/لل) is dropped when the remainder is still a word,
    so ``النيتروجين`` matches ``نيتروجين`` and ``الطماطم`` matches ``طماطم``
    — without it, Arabic retrieval barely fires.
    """
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(normalize_text(text)):
        stripped = _AR_PREFIX_RE.sub("", raw, count=1)
        base = stripped if len(stripped) >= 3 else raw
        tokens.add(base)
        for alt in _SYNONYM_MAP.get(base, ()):
            tokens.add(alt)
    return tokens


def _excerpt(content: str, limit: int = 480) -> str:
    compact = " ".join((content or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


# ======================================================================
# Retrieval
# ======================================================================
def score_document(query_tokens: set[str], document: KnowledgeBase) -> float:
    """Simple but effective overlap scorer (works for AR & EN queries)."""
    if not query_tokens:
        return 0.0
    title_tokens = tokenize(document.title)
    tag_tokens: set[str] = set()
    for tag in document.tags or []:
        tag_tokens |= tokenize(str(tag))
    body_tokens = tokenize(document.content)

    overlap = query_tokens & body_tokens
    score = len(overlap) + 0.6 * len(query_tokens & title_tokens) + 0.4 * len(query_tokens & tag_tokens)
    if not overlap and not (query_tokens & title_tokens):
        return 0.0
    # Prefer precise documents over long, generic ones.
    return score / (1.0 + 0.002 * len(body_tokens))


def retrieve(
    query: str,
    documents: Sequence[KnowledgeBase],
    top_k: int,
) -> list[tuple[KnowledgeBase, float]]:
    """Return the ``top_k`` best matching documents (best first)."""
    query_tokens = tokenize(query)
    scored = [(doc, score_document(query_tokens, doc)) for doc in documents]
    scored = [pair for pair in scored if pair[1] > 0.0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[: max(top_k, 1)]


def citation_cards(
    docs: Sequence[KnowledgeBase], answered_text: str = ""
) -> list[dict[str, Any]]:
    """Build client-ready citation cards; mark cards actually referenced."""
    cited_indexes = {int(m) for m in _CITATION_RE.findall(answered_text or "")}
    cards: list[dict[str, Any]] = []
    for position, doc in enumerate(docs, start=1):
        cards.append(
            {
                "index": position,
                "title": doc.title,
                "source": doc.source,
                "source_url": doc.source_url,
                "category": doc.category,
                "lang": doc.lang,
                "snippet": _excerpt(doc.content),
                "cited": position in cited_indexes,
            }
        )
    return cards


# ======================================================================
# Prompting
# ======================================================================
_SYSTEM_TEMPLATE = """You are Hassad AI (حَصاد AI), an expert agronomist assistant working with \
smallholder farmers in Arabic-speaking regions.

Answer rules:
1. Prefer the numbered knowledge-base excerpts below; cite them inline with \
their bracketed number, e.g. [1] or [2][3].
2. If the excerpts do not cover the question, still answer with sound agronomy \
principles, and clearly say it is general advice.
3. Answer in the same language as the farmer's last message \
(Arabic question -> Arabic answer, English question -> English answer).
4. Be practical: short paragraphs, concrete actions, correct dose/rate when \
known. Never invent product registration numbers.
5. When mentioning pesticides, always add: follow the local label and the \
pre-harvest interval.
6. You are an assistant, not a replacement for a licensed agronomist — for \
severe or unclear cases, recommend an in-person inspection.

Pre-filled crop context (may be empty):
{crop_context}

Knowledge base excerpts:
{knowledge}
"""

_USER_TEMPLATE = """Conversation so far:
{history}

Farmer: {question}
Hassad AI:"""


def build_context_block(crop_context: dict[str, Any] | None) -> str:
    if not crop_context:
        return "(none)"
    cleaned = {k: v for k, v in crop_context.items() if v not in (None, "", [], {})}
    return json.dumps(cleaned, ensure_ascii=False, indent=2, default=str) if cleaned else "(none)"


def build_knowledge_block(docs: Sequence[KnowledgeBase]) -> str:
    if not docs:
        return "(no matching excerpts)"
    lines: list[str] = []
    for position, doc in enumerate(docs, start=1):
        lines.append(
            f"[{position}] {doc.title} — {doc.source} ({doc.category})\n{_excerpt(doc.content, 900)}"
        )
    return "\n\n".join(lines)


def build_history_block(history: Sequence[dict[str, str]]) -> str:
    if not history:
        return "(empty)"
    return "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history)


def detect_language(question: str, preference: str = "auto") -> str:
    if preference in {"ar", "en"}:
        return preference
    return "ar" if re.search(r"[\u0600-\u06FF]", question or "") else "en"


def build_prompt(
    *,
    question: str,
    docs: Sequence[KnowledgeBase],
    crop_context: dict[str, Any] | None,
    history: Sequence[dict[str, str]],
    lang: str,
) -> str:
    system = _SYSTEM_TEMPLATE.format(
        crop_context=build_context_block(crop_context),
        knowledge=build_knowledge_block(docs),
    )
    user = _USER_TEMPLATE.format(
        history=build_history_block(history),
        question=(question or "").strip(),
    )
    return f"{system}\n[Reply language: {'Arabic' if lang == 'ar' else 'English'}]\n\n{user}"


# ======================================================================
# Generation
# ======================================================================
def _configure_genai() -> Any:
    """Build the Gemini client at call time from the runtime key chain.

    Uses the maintained ``google-genai`` SDK — the legacy
    ``google-generativeai`` package is end-of-life and can no longer reach
    current models (e.g. ``gemini-3.8-flash``).

    The first configured key is used (``GEMINI_API_KEY_1..4`` first, base
    key last — same chain the vision pipeline fails over through), and
    SDK auto-retries are **disabled** (``HttpRetryOptions(attempts=0)``):
    a 429 must degrade to the retrieval fallback, never hammer the daily
    quota inside a hidden backoff loop. Values are never logged/returned.
    """
    settings = get_settings()
    chain = settings.gemini_api_keys
    if not chain:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "gemini_not_configured",
                "message": (
                    "No Gemini API key is configured. Add your free key from "
                    "https://aistudio.google.com/apikey to .env (GEMINI_API_KEY or "
                    "GEMINI_API_KEY_1) and restart the server."
                ),
            }
        )
    from google import genai  # imported lazily: optional at runtime
    from google.genai import types

    return genai.Client(
        api_key=chain[0],
        # One HTTP attempt — SDK retries (429/5XX backoff) switched off.
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=0)
        ),
    )


def _chunk_text(chunk: Any) -> str:
    """Safely extract text from a Gemini chunk (may be empty / blocked)."""
    try:
        return chunk.text or ""
    except Exception:  # noqa: BLE001 - blocked or empty candidates raise here
        return ""


async def stream_answer(
    *,
    question: str,
    docs: Sequence[KnowledgeBase],
    crop_context: dict[str, Any] | None,
    history: Sequence[dict[str, str]],
    lang: str,
    temperature: float | None = None,
    mode_out: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    """Yield the answer token-by-token from Gemini (free tier).

    Never raises for LLM problems: when the key is missing, the SDK fails,
    the stream times out or comes back empty, this degrades to a
    retrieval-only answer and records ``mode_out["mode"] =
    "retrieval_fallback"`` so callers can report the real mode — the
    chatbot must always answer, and must never wait without a deadline.
    """
    settings = get_settings()
    prompt = build_prompt(
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

    if not settings.gemini_configured:
        logger.warning("GEMINI_API_KEY missing — serving retrieval-only fallback answers")
        _degrade("not_configured")
        async for piece in _stream_fallback(docs, lang, reason="not_configured"):
            yield piece
        return

    try:
        client = _configure_genai()
        from google.genai import types
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK import/config failure
        logger.error("Gemini SDK configuration failed: %s", exc)
        _degrade("sdk_error")
        async for piece in _stream_fallback(docs, lang, reason="failed"):
            yield piece
        return

    generation_config = types.GenerateContentConfig(
        temperature=(
            temperature if temperature is not None else settings.GEMINI_TEMPERATURE
        ),
        max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
        top_p=0.95,
        # Free-tier gemini-3.8-flash otherwise spends the whole token budget
        # reasoning, leaving nothing for the visible answer.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    # Every await below carries a deadline: free-tier first-byte latency
    # regularly runs 60-120s, and a hung call must never take the endpoint
    # down with it (GEMINI_TIMEOUT_SECONDS is the total budget).
    budget = float(settings.GEMINI_TIMEOUT_SECONDS)
    deadline = asyncio.get_running_loop().time() + budget

    produced = 0
    try:
        stream = await asyncio.wait_for(
            client.aio.models.generate_content_stream(
                model=settings.GEMINI_MODEL_NAME,
                contents=prompt,
                config=generation_config,
            ),
            timeout=budget,
        )
        while True:
            remaining = max(deadline - asyncio.get_running_loop().time(), 0.05)
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            text = _chunk_text(chunk)
            if text:
                produced += len(text)
                yield text
    except StopAsyncIteration:
        pass
    except asyncio.TimeoutError:
        if produced:
            logger.warning(
                "Gemini stream hit its %.0fs budget after %d chars — returning the partial answer",
                budget,
                produced,
            )
            return
        logger.warning(
            "Gemini produced nothing within %.0fs (free-tier latency) — retrieval fallback",
            budget,
        )
        _degrade("timeout")
        async for piece in _stream_fallback(docs, lang, reason="timeout"):
            yield piece
        return
    except Exception as exc:  # noqa: BLE001 - network / quota / safety errors
        if produced:
            logger.error("Gemini streaming failed after %d chars: %s", produced, exc)
            return  # keep whatever was already streamed to the client
        logger.error("Gemini streaming failed: %s", exc)
        _degrade("failed")
        async for piece in _stream_fallback(docs, lang, reason="failed"):
            yield piece
        return

    if produced == 0:
        logger.warning("Gemini returned an empty stream — retrieval fallback")
        _degrade("empty")
        async for piece in _stream_fallback(docs, lang, reason="empty"):
            yield piece


async def _stream_fallback(
    docs: Sequence[KnowledgeBase], lang: str, *, reason: str = "not_configured"
) -> AsyncIterator[str]:
    """Retrieval-only answer used whenever Gemini cannot answer right now."""
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
        intro = intros.get(reason, intros["failed"])
        outro = outros.get(reason, outros["failed"])
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
    for card in citation_cards(docs):
        yield f"[{card['index']}] {card['title']}: {card['snippet']}\n"
    yield outro


async def answer_once(
    *,
    question: str,
    docs: Sequence[KnowledgeBase],
    crop_context: dict[str, Any] | None,
    history: Sequence[dict[str, str]],
    lang: str,
    temperature: float | None = None,
    mode_out: dict[str, str] | None = None,
) -> str:
    """Non-streaming helper (used by /chatbot/ask and by tests)."""
    parts: list[str] = []
    try:
        async for piece in stream_answer(
            question=question,
            docs=docs,
            crop_context=crop_context,
            history=history,
            lang=lang,
            temperature=temperature,
            mode_out=mode_out,
        ):
            parts.append(piece)
    except HTTPException:
        if parts:  # keep whatever was already generated
            return "".join(parts)
        raise
    return "".join(parts)


# ======================================================================
# Data access helpers
# ======================================================================
async def load_knowledge_base(db: AsyncSession) -> list[KnowledgeBase]:
    """Load the whole (small) knowledge base for in-process scoring."""
    result = await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.category))
    return list(result.scalars().all())


async def build_crop_context(
    db: AsyncSession, user_id: str, *, plot_id: str | None = None
) -> dict[str, Any]:
    """Assemble pre-filled crop context for the RAG prompt."""
    from app.models.diagnosis import Diagnosis
    from app.models.farm import Farm, Plot

    context: dict[str, Any] = {}

    if plot_id:
        row = await db.execute(
            select(Plot, Farm)
            .join(Farm, Plot.farm_id == Farm.id)
            .where(Plot.id == plot_id, Farm.owner_id == user_id)
        )
        plot, farm = row.first() or (None, None)
        if plot is not None and farm is not None:
            context["plot"] = {
                "name": plot.name,
                "farm": farm.name,
                "region": farm.location_text,
                "current_crop": plot.current_crop,
                "soil_type": plot.soil_type,
                "irrigation": plot.irrigation_method,
                "planted_at": plot.planted_at.isoformat() if plot.planted_at else None,
                "expected_harvest_at": (
                    plot.expected_harvest_at.isoformat() if plot.expected_harvest_at else None
                ),
            }

    diagnosis_query = select(Diagnosis).where(
        Diagnosis.farmer_id == user_id,
        Diagnosis.is_deleted.is_(False),
    )
    if plot_id:
        diagnosis_query = diagnosis_query.where(Diagnosis.plot_id == plot_id)
    diagnosis_query = diagnosis_query.order_by(Diagnosis.updated_at.desc()).limit(1)

    diagnosis = (await db.execute(diagnosis_query)).scalar_one_or_none()
    if diagnosis is not None:
        context["last_diagnosis"] = {
            "crop": diagnosis.crop_label,
            "disease": diagnosis.disease_label,
            "disease_confidence": diagnosis.disease_confidence,
            "status": diagnosis.status,
        }
        if diagnosis.crop_label:
            context.setdefault("current_crop", diagnosis.crop_label)

    return context


# ======================================================================
# Knowledge-base seed (first boot)
# ======================================================================
KNOWLEDGE_SEED: list[dict[str, Any]] = [
    {
        "slug": "irrigation-basics",
        "title": "Smart irrigation scheduling / جدولة ريّ ذكية",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/land-water/water-and-food-security/en/",
        "category": "water",
        "lang": "both",
        "tags": ["irrigation", "water", "ري", "مياه", "جدولة"],
        "content": (
            "Irrigation: water the soil, not the foliage, and do it early in the morning. "
            "Most vegetables need 25-40 mm per week during fruiting; sandy soils need more "
            "frequent, smaller doses. Check moisture 10-15 cm deep with a stick or your hand: "
            "if the soil does not hold its shape, irrigate.\n"
            "الريّ: اسقِ التربة لا الأوراق ويفضّل صباحاً. معظم الخضروات تحتاج 25-40 ملم أسبوعياً "
            "أثناء الإثمار، والتربة الرملية تحتاج حصصاً صغيرة متكررة. تحقق من الرطوبة على عمق "
            "10-15 سم؛ إن لم تتشكل التربة في يدك فحان وقت الري."
        ),
    },
    {
        "slug": "npk-fertiliser-balance",
        "title": "Balanced NPK fertilisation / توازن الأسمدة الآزوتية والفوسفورية والبوتاسية",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/soils-portal/en/",
        "category": "nutrition",
        "lang": "both",
        "tags": ["fertiliser", "npk", "nitrogen", "أسمدة", "نيتروجين", "تربة"],
        "content": (
            "Fertiliser: base application on a soil test. Excess nitrogen produces lush foliage "
            "that attracts aphids and diseases; phosphorus supports roots and flowering; "
            "potassium improves fruit quality and drought tolerance. Split nitrogen into 2-3 "
            "dressings instead of one heavy application.\n"
            "الأسمدة: اعتمد على تحليل التربة. الإفراط في الآزوت يُنمّي الأوراق ويجذب الحشرات "
            "والأمراض، والفوسفور يدعم الجذور والإزهار، والبوتاسيوم يحسّن جودة الثمار ومقاومة "
            "الجفاف. وزّع جرعات الآزوت على 2-3 دفعات بدلاً من جرعة واحدة."
        ),
    },
    {
        "slug": "ipm-pest-management",
        "title": "Integrated Pest Management / إدارة الآفات المتكاملة",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/pest-management/en/",
        "category": "protection",
        "lang": "both",
        "tags": ["ipm", "pest", "spray", "آفات", "مبيدات", "إدارة"],
        "content": (
            "IPM order of action: 1) monitor weekly with scouting and sticky traps, "
            "2) remove infected material, 3) conserve natural enemies (ladybirds, parasitoid "
            "wasps), 4) use biological products (Bacillus, Trichogramma), 5) spray selective "
            "chemistry only above the economic threshold, rotating modes of action. "
            "Always respect the label rate and the pre-harvest interval.\n"
            "إدارة الآفات المتكاملة: 1) راقب أسبوعياً بالفحص والمصائد اللاصقة، 2) أزل الأجزاء "
            "المصابة، 3) احمِ الأعداء الطبيعيين، 4) استخدم المنتجات الحيوية، 5) رشّ الكيميائيات "
            "الانتقائية فقط عند تجاوز العتبة الاقتصادية مع تدوير مواد الفعالية. التزم بالجرعة "
            "المذكورة في الملصق وفترة الأمان قبل الحصاد."
        ),
    },
    {
        "slug": "soil-health-organic-matter",
        "title": "Soil health & organic matter / صحة التربة والمادة العضوية",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/soils-portal/soil-management/soil-carbon/en/",
        "category": "soil",
        "lang": "both",
        "tags": ["soil", "compost", "organic", "تربة", "سماد عضوي", "كومبوست"],
        "content": (
            "Healthy soil: keep it covered (mulch or cover crops), add 5-10 t/ha of mature "
            "compost annually, minimise tillage, and rotate with legumes to fix nitrogen. "
            "A soil rich in organic matter holds water longer and suppresses soil-borne disease.\n"
            "التربة السليمة: حافظ على تغطيتها (خلية أو محاصيل تغطية)، أضف 5-10 طن/هكتار سماداً "
            "عضوياً ناضجاً سنوياً، قلّل الحراثة، وطبّق التناوب مع البقوليات. التربة الغنية "
            "بالمادة العضوية تحفظ الماء أطول وتكبح أمراض التربة."
        ),
    },
    {
        "slug": "tomato-late-blight-guide",
        "title": "Tomato late blight field guide / دليل اللفحة المتأخرة في الطماطم",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.cabi.org/isc/datasheet/infestans",
        "category": "disease",
        "lang": "both",
        "tags": ["late blight", "tomato", "phytophthora", "لفحة", "طماطم", "متأخرة"],
        "content": (
            "Late blight: dark water-soaked lesions with a white fungal edge on leaves, stems "
            "and fruit; overnight humidity above 90% triggers epidemics. Remove infected plants, "
            "stop overhead irrigation, apply a preventive fungicide before rain, and destroy "
            "residues after harvest. Resistant varieties plus 2-season rotation with cereals "
            "give the best long-term control.\n"
            "اللفحة المتأخرة: بقع داكنة مبللة بحافة بيضاء على الأوراق والسيقان والثمار، ورطوبة "
            "الليل فوق 90% تطلق الوباء. أزل النباتات المصابة، أوقف الري بالرشّ، طبّق مبيداً "
            "وقائياً قبل المطر، وأتلف بقايا المحصول بعد الحصاد. الأصناف المقاومة مع التناوب "
            "لموسمين مع الحبوب تعطي أفضل نتيجة طويلة المدى."
        ),
    },
    {
        "slug": "wheat-rust-management",
        "title": "Wheat rust management / إدارة صدأ القمح",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.cabi.org/isc/dataset/233/",
        "category": "disease",
        "lang": "both",
        "tags": ["rust", "wheat", "stripe rust", "صدأ", "قمح"],
        "content": (
            "Rust: orange-brown pustules in stripes (stripe rust) or scattered (leaf rust) on "
            "leaves; wind spreads spores over kilometres. Plant early, use resistant seed lots, "
            "apply a triazole at first pustules and keep the field weed-free to reduce humidity.\n"
            "الصدأ: بثور برتقالية بنية على شكل خطوط (الصدأ المخطط) أو متناثرة (الصدأ الورقي)؛ "
            "تنتشر أبواغها بالرياح لمسافات طويلة. ازرع مبكراً، استخدم بذوراً مقاومة، طبّق "
            "مبيداً تريازولياً عند أول بثور، ونظّف الحقل من الأعشاب لخفض الرطوبة."
        ),
    },
    {
        "slug": "maize-planting-guide",
        "title": "Maize planting & spacing / زراعة الذرة والمسافات",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/maize/en/",
        "category": "agronomy",
        "lang": "both",
        "tags": ["maize", "corn", "planting", "spacing", "ذرة", "زراعة", "مسافات"],
        "content": (
            "Maize: sow at 5-7 cm depth in warm soil (above 12 C), 75 cm between rows and "
            "20-25 cm within the row for rainfed systems; thin to the strongest seedling. "
            "Apply 40-60 kg/ha nitrogen at planting and the rest at knee-high stage.\n"
            "الذرة: ازرع على عمق 5-7 سم في تربة دافئة (فوق 12 درجة)، بمسافة 75 سم بين الصفوف "
            "و20-25 سم داخل الصف في الأنظمة المطرية، وخفّف النباتات تاركاً الأقوى. أضف 40-60 "
            "كجم/هكتار آزوت عند الزراعة والباقي في مرحلة الركبة."
        ),
    },
    {
        "slug": "postharvest-handling",
        "title": "Post-harvest handling & storage / ما بعد الحصاد والتخزين",
        "source": "Hassad AI Knowledge Base",
        "source_url": "https://www.fao.org/food-loss-reduction/en/",
        "category": "postharvest",
        "lang": "both",
        "tags": ["postharvest", "storage", "cold chain", "تخزين", "بعد الحصاد"],
        "content": (
            "Post-harvest: harvest in the cool morning, shade produce immediately, avoid bruising, "
            "and move to ventilated storage the same day. Every hour at field heat shortens shelf "
            "life; a simple evaporative cooler can add several days for leafy vegetables.\n"
            "بعد الحصاد: اجمع في الصباح البارد، ظلّل المحصول فوراً، تجنّب الطرق، وانقله في نفس "
            "اليوم إلى مخزن جيد التهوية. كل ساعة في حرارة الحقل تقصر عمر التخزين، والمبرّد "
            "التبخيري البسيط يضيف عدة أيام للأوراق الخضراء."
        ),
    },
]


async def seed_knowledge_base(db: AsyncSession) -> int:
    """Insert seed documents when the table is empty (idempotent)."""
    existing = (await db.execute(select(KnowledgeBase.slug))).scalars().first()
    if existing is not None:
        return 0

    for entry in KNOWLEDGE_SEED:
        db.add(
            KnowledgeBase(
                slug=entry["slug"],
                title=entry["title"],
                source=entry["source"],
                source_url=entry["source_url"],
                category=entry["category"],
                lang=entry["lang"],
                content=entry["content"],
                tags=entry.get("tags", []),
            )
        )
    await db.commit()
    logger.info("Seeded knowledge base with %d documents", len(KNOWLEDGE_SEED))
    return len(KNOWLEDGE_SEED)
