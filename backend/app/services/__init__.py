"""AI service adapters (local KB + image quality gate). The Gemini-facing
logic moved to the v2 multi-agent framework in ``app.agents``."""

from app.agents.knowledge import seed_knowledge_base

__all__ = ["seed_knowledge_base"]
