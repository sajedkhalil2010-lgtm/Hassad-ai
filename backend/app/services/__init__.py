"""AI service adapters (local KB + image quality gate). All AI reasoning
lives in the v4 multi-agent framework in ``app.agents``."""

from app.agents.knowledge import seed_knowledge_base

__all__ = ["seed_knowledge_base"]
