"""Chatbot models: sessions, messages and the offline knowledge base.

The knowledge base powers the chat answers: excerpts are retrieved with
a language-agnostic (Arabic + English) lexical scorer, assembled into
the local answer and returned to the client as **citation cards**.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, new_id, utcnow


class ChatSession(Base):
    """A conversation thread, optionally bound to a plot for crop context."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plots.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(160), nullable=False, default="New conversation")
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="ar")
    crop_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    client_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_chat_sessions_user_id_client_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ChatSession {self.id} title={self.title!r}>"


class ChatMessage(Base):
    """One turn inside a :class:`ChatSession` (user or assistant)."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Knowledge-base citation cards produced for this message.
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    session = relationship("ChatSession", back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ChatMessage {self.role} session={self.session_id}>"


class KnowledgeBase(Base):
    """A citable agronomy document/excerpt used by the RAG pipeline."""

    __tablename__ = "knowledge_base"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="Hassad AI KB")
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="general")
    #: ar | en | both — lets the client pick the right card per language.
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="both")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_knowledge_base_category_lang", "category", "lang"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<KnowledgeBase {self.slug}>"
