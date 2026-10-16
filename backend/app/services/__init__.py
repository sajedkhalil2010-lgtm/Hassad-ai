"""AI service adapters (Gemini vision + Gemini RAG over the local KB)."""

from app.services.rag_service import seed_knowledge_base

__all__ = ["seed_knowledge_base"]
