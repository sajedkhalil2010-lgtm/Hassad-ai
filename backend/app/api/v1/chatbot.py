"""Agronomy RAG chatbot — streaming answers with knowledge-base citations.

* ``POST /chatbot/sessions``                      — create a thread (optionally bound to a plot)
* ``POST /chatbot/sessions/{id}/messages``        — **Server-Sent Events** stream of the answer
* ``POST /chatbot/ask``                           — one-shot, non-streaming convenience endpoint
* ``GET  /chatbot/sessions/{id}/messages``        — conversation history
* ``GET  /chatbot/knowledge``                     — the citable knowledge base

Privacy: only text is processed — no photos are sent to the LLM, and the
pre-filled crop context contains plot/diagnosis metadata chosen by the farmer.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.database import get_session_factory, new_id, utcnow
from app.models.chatbot import ChatMessage, ChatSession, KnowledgeBase
from app.models.farm import Farm, Plot
from app.schemas.diagnosis import (
    ChatAskRequest,
    ChatAskResponse,
    ChatMessageIn,
    ChatMessageOut,
    ChatSessionCreate,
    ChatSessionOut,
    CitationCard,
)
from app.services.rag_service import (
    answer_once,
    build_crop_context,
    citation_cards,
    detect_language,
    load_knowledge_base,
    retrieve,
    stream_answer,
)

logger = logging.getLogger("hassad.api.chatbot")

router = APIRouter(prefix="/chatbot", tags=["Chatbot"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
}


def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event (UTF-8, Arabic-safe)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _session_out(session: ChatSession, message_count: int = 0) -> ChatSessionOut:
    return ChatSessionOut(
        id=session.id,
        client_id=session.client_id,
        title=session.title,
        plot_id=session.plot_id,
        locale=session.locale,
        crop_context=session.crop_context,
        message_count=message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


async def _owned_session(db, user_id: str, session_id: str) -> ChatSession:  # noqa: ANN001
    chat_session = await db.get(ChatSession, session_id)
    if chat_session is None or chat_session.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return chat_session


async def _verify_plot_owner(db, user_id: str, plot_id: str | None) -> None:  # noqa: ANN001
    if not plot_id:
        return
    result = await db.execute(
        select(Plot.id).join(Farm, Plot.farm_id == Farm.id).where(
            Plot.id == plot_id, Farm.owner_id == user_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plot not found")


async def _merged_context(db, chat_session: ChatSession) -> dict:  # noqa: ANN001
    """Plot/diagnosis context merged with any client-supplied context."""
    context = await build_crop_context(db, chat_session.user_id, plot_id=chat_session.plot_id)
    if chat_session.crop_context:
        context.update(chat_session.crop_context)
    return context


async def _persist_assistant_message(
    *,
    session_id: str,
    content: str,
    citations: list[dict],
    mode: str,
) -> str | None:
    """Persist the assistant turn using its own session (stream-safe)."""
    if not content.strip():
        return None
    try:
        async with get_session_factory()() as db:
            message = ChatMessage(
                id=new_id(),
                session_id=session_id,
                role="assistant",
                content=content,
                citations=citations,
                meta={"mode": mode},
            )
            db.add(message)
            await db.execute(
                ChatSession.__table__.update()
                .where(ChatSession.__table__.c.id == session_id)
                .values(updated_at=utcnow())
            )
            await db.commit()
            return message.id
    except Exception:  # noqa: BLE001 - never break the response because of persistence
        logger.exception("Failed to persist assistant message for session %s", session_id)
        return None


# ======================================================================
# Sessions
# ======================================================================
@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: ChatSessionCreate, db: DbSession, user: CurrentUser) -> ChatSessionOut:
    """Create a conversation thread, optionally bound to a plot.

    Idempotent on ``(user_id, client_id)``: if the unique constraint fires
    (offline sync, retry, legacy constant client ids) the existing thread is
    returned instead of an error.
    """
    user_id = user.id  # capture before commit/rollback expires the instance
    await _verify_plot_owner(db, user_id, payload.plot_id)

    chat_session = ChatSession(
        id=new_id(),
        client_id=payload.client_id,
        user_id=user_id,
        title=payload.title or "New conversation",
        plot_id=payload.plot_id,
        locale=payload.locale,
    )
    context = await build_crop_context(db, user_id, plot_id=payload.plot_id)
    if payload.crop_context:
        context.update(payload.crop_context)
    chat_session.crop_context = context or None

    db.add(chat_session)
    try:
        await db.commit()
    except IntegrityError:
        # unique (user_id, client_id) already exists — idempotent get-or-create
        await db.rollback()
        existing = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.user_id == user_id,
                    ChatSession.client_id == payload.client_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        logger.info(
            "create_session: client_id collision for user %s — returning existing session",
            user_id,
        )
        return _session_out(existing)
    await db.refresh(chat_session)
    return _session_out(chat_session)


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[ChatSessionOut]:
    """List the caller's conversation threads (newest first)."""
    rows = (
        await db.execute(
            select(ChatSession, func.count(ChatMessage.id).label("message_count"))
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.user_id == user.id)
            .group_by(ChatSession.id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_session_out(session, int(count)) for session, count in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
async def get_session(session_id: str, db: DbSession, user: CurrentUser) -> ChatSessionOut:
    """Fetch one conversation thread."""
    chat_session = await _owned_session(db, user.id, session_id)
    count = (
        await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
        )
    ).scalar_one()
    return _session_out(chat_session, int(count))


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    session_id: str,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(200, ge=1, le=500),
) -> list[ChatMessageOut]:
    """Full conversation history of a thread."""
    await _owned_session(db, user.id, session_id)
    rows = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [ChatMessageOut.model_validate(row) for row in rows]


# ======================================================================
# Streaming chat
# ======================================================================
@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: ChatMessageIn,
    db: DbSession,
    user: CurrentUser,
) -> StreamingResponse:
    """Send a message and stream the answer back as Server-Sent Events.

    Events: ``chunk`` (``{"delta": "..."}``) → ``citations`` → ``done``;
    on failure an ``error`` event is emitted instead (the stream never breaks
    silently).
    """
    settings = get_settings()
    if len(payload.content) > settings.CHAT_MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Message is too long (max {settings.CHAT_MAX_INPUT_CHARS} characters)",
        )

    chat_session = await _owned_session(db, user.id, session_id)

    # Persist the farmer's turn first (survives client disconnects).
    user_message = ChatMessage(
        id=new_id(), session_id=session_id, role="user", content=payload.content
    )
    db.add(user_message)
    # Auto-title the thread on the first message.
    if chat_session.title in {"New conversation", "محادثة جديدة"}:
        chat_session.title = payload.content[:60]
    chat_session.updated_at = utcnow()
    db.add(chat_session)
    await db.commit()

    context = await _merged_context(db, chat_session)

    history_rows = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(settings.CHAT_HISTORY_MAX_MESSAGES)
            )
        )
        .scalars()
        .all()
    )
    history = [
        {"role": row.role, "content": row.content}
        for row in reversed(history_rows)
        if row.id != user_message.id
    ]

    knowledge = await load_knowledge_base(db)
    retrieval_query = _retrieval_query(payload.content, context)
    retrieved_docs = [doc for doc, _score in retrieve(retrieval_query, knowledge, settings.RAG_TOP_K)]
    lang = detect_language(payload.content, payload.lang)

    async def event_stream():  # noqa: ANN202 - async generator of SSE strings
        parts: list[str] = []
        mode = "gemini" if settings.gemini_configured else "retrieval_fallback"
        state: dict[str, str] = {"mode": mode}
        try:
            async for piece in stream_answer(
                question=payload.content,
                docs=retrieved_docs,
                crop_context=context,
                history=history,
                lang=lang,
                temperature=payload.temperature,
                mode_out=state,
            ):
                parts.append(piece)
                yield _sse("chunk", {"type": "chunk", "delta": piece})
        except HTTPException as exc:
            logger.warning("Chat stream failed: %s", exc.detail)
            await _persist_assistant_message(
                session_id=session_id,
                content="".join(parts),
                citations=citation_cards(retrieved_docs, "".join(parts)),
                mode=f"{mode}:error",
            )
            yield _sse("error", {"type": "error", "detail": exc.detail})
            return
        except Exception as exc:  # noqa: BLE001 - never leave the stream hanging
            logger.exception("Unexpected chat stream failure")
            yield _sse(
                "error",
                {
                    "type": "error",
                    "detail": {
                        "code": "chat_failed",
                        "message": f"The assistant failed unexpectedly ({type(exc).__name__}).",
                    },
                },
            )
            return

        mode = state["mode"]  # may have degraded to retrieval mid-stream
        full_answer = "".join(parts)
        cards = citation_cards(retrieved_docs, full_answer)
        message_id = await _persist_assistant_message(
            session_id=session_id, content=full_answer, citations=cards, mode=mode
        )
        yield _sse("citations", {"type": "citations", "citations": cards})
        yield _sse(
            "done",
            {
                "type": "done",
                "message_id": message_id,
                "session_id": session_id,
                "mode": mode,
                "lang": lang,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _retrieval_query(question: str, context: dict) -> str:
    """Blend the question with crop context so retrieval matches agronomy terms."""
    parts = [question]
    current_crop = context.get("current_crop") or (context.get("plot") or {}).get("current_crop")
    if current_crop:
        parts.append(str(current_crop))
    last = context.get("last_diagnosis") or {}
    for key in ("disease", "crop"):
        if last.get(key):
            parts.append(str(last[key]))
    return " ".join(p for p in parts if p)


# ======================================================================
# One-shot + knowledge base
# ======================================================================
@router.post("/ask", response_model=ChatAskResponse)
async def ask(payload: ChatAskRequest, db: DbSession, user: CurrentUser) -> ChatAskResponse:
    """Non-streaming question (optional plot context; optional session logging)."""
    settings = get_settings()
    if payload.plot_id:
        await _verify_plot_owner(db, user.id, payload.plot_id)

    context = await build_crop_context(db, user.id, plot_id=payload.plot_id)
    knowledge = await load_knowledge_base(db)
    retrieved_docs = [
        doc for doc, _ in retrieve(_retrieval_query(payload.question, context), knowledge, settings.RAG_TOP_K)
    ]
    lang = detect_language(payload.question, payload.lang)

    state: dict[str, str] = {"mode": "gemini" if settings.gemini_configured else "retrieval_fallback"}
    answer = await answer_once(
        question=payload.question,
        docs=retrieved_docs,
        crop_context=context,
        history=[],
        lang=lang,
        mode_out=state,
    )
    cards = citation_cards(retrieved_docs, answer)
    mode = state["mode"]
    if mode == "gemini":
        notice = None
    elif state.get("reason") == "not_configured":
        notice = "GEMINI_API_KEY not configured — retrieval-only answer."
    else:
        notice = f"Gemini unavailable ({state.get('reason', 'unknown')}) — retrieval-only answer."

    if payload.session_id:
        chat_session = await _owned_session(db, user.id, payload.session_id)
        db.add(
            ChatMessage(
                id=new_id(), session_id=chat_session.id, role="user", content=payload.question
            )
        )
        db.add(
            ChatMessage(
                id=new_id(),
                session_id=chat_session.id,
                role="assistant",
                content=answer,
                citations=cards,
                meta={"mode": mode},
            )
        )
        chat_session.updated_at = utcnow()
        db.add(chat_session)
        await db.commit()

    return ChatAskResponse(
        answer=answer,
        citations=[CitationCard.model_validate(card) for card in cards],
        mode=mode,  # type: ignore[arg-type]
        session_id=payload.session_id,
        notice=notice,
    )


@router.get("/knowledge", response_model=list[CitationCard])
async def knowledge(
    db: DbSession,
    _: CurrentUser,
    category: str | None = Query(None, description="Filter by category"),
    lang: str | None = Query(None, pattern="^(ar|en|both)$"),
    limit: int = Query(50, ge=1, le=200),
) -> list[CitationCard]:
    """Browse the citable knowledge base (the RAG retrieval corpus)."""
    query = select(KnowledgeBase)
    if category:
        query = query.where(KnowledgeBase.category == category)
    if lang:
        query = query.where(KnowledgeBase.lang == lang)
    rows = (
        (await db.execute(query.order_by(KnowledgeBase.category).limit(limit))).scalars().all()
    )
    return [CitationCard.model_validate(card) for card in citation_cards(rows)]
